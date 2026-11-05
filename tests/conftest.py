"""Shared pytest setup.

The two database test files (tests/test_wages_shifts.py, tests/test_attendance_rule_overtime_salary.py) read their
connection from TEST_PG_* / DB_TEST_* variables. To keep ONE place to fill in, those variables are read from the
backend `.env` file (the same file the app uses) when they are not already set in the environment, and
DB_TEST_* falls back to TEST_PG_*. Nothing else is read from `.env` here: every DB-free test ignores it.
"""
import os
from pathlib import Path

_ENV = Path(__file__).resolve().parents[1] / ".env"
_KEYS = (
    "TEST_PG_HOST", "TEST_PG_PORT", "TEST_PG_USER", "TEST_PG_PASSWORD",
    "DB_TEST_HOST", "DB_TEST_PORT", "DB_TEST_USER", "DB_TEST_PASSWORD",
    "PGSSLMODE",
)


def _read_env_file(path: Path) -> dict:
    out = {}
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key in _KEYS:
                out[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _load_test_connection() -> None:
    values = _read_env_file(_ENV)
    for key, value in values.items():
        if value and key not in os.environ:
            os.environ[key] = value
    # the second DB test file uses DB_TEST_*: mirror TEST_PG_* so one set of lines is enough
    for suffix in ("HOST", "PORT", "USER", "PASSWORD"):
        if f"DB_TEST_{suffix}" not in os.environ and f"TEST_PG_{suffix}" in os.environ:
            os.environ[f"DB_TEST_{suffix}"] = os.environ[f"TEST_PG_{suffix}"]


_load_test_connection()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _face_backend_never_real(monkeypatch):
    """SAFETY: no test may reach AWS Rekognition or the user_face_index table (the backend `.env` holds real settings).
    Every test starts with the in-memory mock face backend selected; tests that exercise the Rekognition backend pass
    fake clients / fake stores explicitly (tests/test_face_service.py, tests/test_rekognition_face.py)."""
    monkeypatch.setenv("FACE_BACKEND", "mock")
    monkeypatch.delenv("APP_ENV", raising=False)
    try:
        from ml import face_service
    except Exception:  # pragma: no cover - ml package not importable in some stripped environment
        yield
        return
    face_service.set_backend(None)
    yield
    face_service.set_backend(None)
