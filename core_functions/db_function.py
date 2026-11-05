from fastapi import HTTPException
import psycopg2
from psycopg2 import pool
import pytz
from psycopg2.extras import RealDictCursor
import logging
import os
import re
import threading
from dotenv import load_dotenv

load_dotenv()

ist = pytz.timezone('Asia/Kolkata')
logger = logging.getLogger(__name__)

_connection_pool = None

_VALID_SCHEMA_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

def _validate_schema(schema: str) -> str:
    if not _VALID_SCHEMA_RE.match(schema):
        raise ValueError(f"DB_SCHEMA contains invalid characters (only alphanumeric and underscore allowed)")
    return schema

def get_db_config():
    """Get database configuration from environment variables"""
    schema = os.getenv('DB_SCHEMA')
    if not schema:
        raise ValueError("DB_SCHEMA environment variable is required")
    _validate_schema(schema)
    return {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME'),
        'port': int(os.getenv('DB_PORT', 5432)),
        # Session time zone is pinned to UTC (decision D-2): timestamptz math and now() never depend on the server setting.
        # Every connection that uses this config (the pool and face_metrics) gets both settings at connect time.
        'options': f"-c search_path={schema} -c timezone=UTC"
    }

_pool_init_lock = threading.Lock()


def initialize_connection_pool():
    """Initialize connection pool"""
    global _connection_pool
    with _pool_init_lock:
        if _connection_pool is None:
            try:
                db_cred = get_db_config()
                # Sync routes run on worker threads: the plain SimpleConnectionPool is not thread-safe.
                _connection_pool = pool.ThreadedConnectionPool(5, 20, **db_cred)
                logger.info(f"Database connection pool initialized with schema: {os.getenv('DB_SCHEMA')}")
            except Exception as e:
                logger.error(f"Failed to initialize connection pool: {e}")
                raise


def _discard_connection(conn):
    """Give a connection that cannot be used back to the pool *closed*, so its slot is freed and never reused."""
    try:
        _connection_pool.putconn(conn, close=True)
    except Exception as e:
        logger.warning(f"Error discarding connection: {e}")
        try:
            conn.close()
        except Exception:
            pass


def get_db_connection():
    """Get connection from pool"""
    global _connection_pool
    if _connection_pool is None:
        initialize_connection_pool()
    last_error = None
    # Two tries: the first connection may have gone stale (server restart, idle timeout); a fresh one is opened for the retry.
    for _attempt in range(2):
        try:
            conn = _connection_pool.getconn()
        except Exception as e:
            logger.error(f"Failed to get connection from pool: {e}")
            raise HTTPException(status_code=500, detail="Database connection failed")
        try:
            if conn.closed:
                raise psycopg2.OperationalError("connection from pool is closed")
            schema = _validate_schema(os.getenv('DB_SCHEMA', ''))
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {schema}")
            conn.commit()
            return conn
        except Exception as e:
            # The connection was taken from the pool: never leave it checked out on a failed set-up.
            last_error = e
            _discard_connection(conn)
    logger.error(f"Failed to get connection from pool: {last_error}")
    raise HTTPException(status_code=500, detail="Database connection failed")


def return_connection(conn):
    """Return connection to pool"""
    global _connection_pool
    if conn and _connection_pool:
        try:
            if conn.closed:
                # putconn would drop a closed connection too, but only after it had tried to roll it back.
                _discard_connection(conn)
                return
            _connection_pool.putconn(conn)
        except Exception as e:
            # e.g. the rollback of a broken connection failed inside putconn, which leaves its slot marked "in use".
            logger.warning(f"Error returning connection: {e}")
            _discard_connection(conn)

def close_connection(conn):
    """Return connection to pool"""
    return_connection(conn)


