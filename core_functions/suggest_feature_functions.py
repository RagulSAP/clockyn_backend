import boto3
import os
import logging
import threading
from datetime import datetime
from html import escape as html_escape
from utils import branding
from utils.timezone import get_utc_now, IST
from core_functions.db_function import get_db_connection, return_connection

logger = logging.getLogger(__name__)


def _send_suggestion_emails(subject: str, text: str, org_id: str):
    """Send suggestion email to all active_mail entries with status=1 via AWS SES."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT email FROM active_mail WHERE status = 1"
        )
        recipients = [row[0] for row in cursor.fetchall()]
    finally:
        cursor.close()
        return_connection(conn)

    if not recipients:
        return

    email_user = os.getenv('EMAIL_USER')
    ses_region = os.getenv('AWS_SES_REGION', 'ap-south-1')
    if not email_user:
        logger.error("EMAIL_USER not configured")
        return

    ses = boto3.client('ses', region_name=ses_region)
    html_body = f"""
    <html><body style="font-family:Arial,sans-serif;padding:24px;">
      <h2 style="color:#6366f1;">New Feature Suggestion - {html_escape(branding.app_name())}</h2>
      <p><b>Org ID:</b> {org_id}</p>
      <p><b>Subject:</b> {subject}</p>
      <hr/>
      <p style="white-space:pre-wrap;">{text}</p>
    </body></html>
    """

    for recipient in recipients:
        try:
            ses.send_email(
                Source=email_user,
                Destination={'ToAddresses': [recipient]},
                Message={
                    'Subject': {'Data': f'[Feature Suggestion] {subject}'},
                    'Body': {'Html': {'Data': html_body}},
                },
            )
            logger.info(f"Suggestion email sent to {recipient}")
        except Exception as e:
            logger.error(f"Failed to send suggestion email to {recipient}: {e}")


def get_suggestions() -> list:
    """Fetch all suggestions from email_log ordered by newest first."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT el.id AS log_id, el.org_id, el.date, el.subject, el."text",
                   el.created_at, oi.org_name AS company_name
            FROM email_log el
            LEFT JOIN org_info oi ON oi.org_id = el.org_id
            ORDER BY el.created_at DESC NULLS LAST
            """
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            item['content'] = item.get('text', '')
            item['created_at'] = item['created_at'].isoformat() if item.get('created_at') else ''
            item['date'] = str(item['date']) if item.get('date') else ''
            result.append(item)
        return result
    finally:
        cursor.close()
        return_connection(conn)


def submit_suggest_feature(org_id: str, subject: str, text: str) -> dict:
    """Store suggestion in email_log and send to all active recipients."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        now = get_utc_now()
        cursor.execute(
            """
            INSERT INTO email_log (org_id, date, subject, "text", created_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (org_id, now.date(), subject, text, now),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)

    thread = threading.Thread(
        target=_send_suggestion_emails, args=(subject, text, org_id), daemon=True
    )
    thread.start()

    return {"success": True, "message": "Suggestion submitted successfully"}


def reply_to_suggestion(log_id: int, reply_text: str, admin_name: str = "Admin") -> dict:
    """Store an admin reply to a suggestion."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        now = get_utc_now()
        cursor.execute(
            """
            INSERT INTO suggestion_replies (email_log_id, reply_text, admin_name, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (log_id, reply_text, admin_name, now),
        )
        conn.commit()
        return {"success": True, "message": "Reply sent successfully"}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()
        return_connection(conn)


def get_my_suggestions(org_id: str) -> list:
    """Fetch suggestions for a specific org with admin replies (for app users)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT el.id AS log_id, el.org_id, el.subject, el."text" AS content,
                   el.created_at,
                   COALESCE(
                       json_agg(
                           json_build_object(
                               'reply_text', sr.reply_text,
                               'admin_name', sr.admin_name,
                               'created_at', sr.created_at
                           ) ORDER BY sr.created_at ASC
                       ) FILTER (WHERE sr.id IS NOT NULL),
                       '[]'::json
                   ) AS replies
            FROM email_log el
            LEFT JOIN suggestion_replies sr ON sr.email_log_id = el.id
            WHERE el.org_id = %s
            GROUP BY el.id, el.org_id, el.subject, el."text", el.created_at
            ORDER BY el.created_at DESC NULLS LAST
            """,
            (org_id,)
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            item['created_at'] = item['created_at'].isoformat() if item.get('created_at') else ''
            result.append(item)
        return result
    finally:
        cursor.close()
        return_connection(conn)


def get_suggestions_with_reply_count() -> list:
    """Fetch all suggestions with reply count + full reply history for admin view."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT el.id AS log_id, el.org_id, el.date, el.subject, el."text",
                   el.created_at, oi.org_name AS company_name,
                   COUNT(sr.id) AS reply_count,
                   MAX(sr.created_at) AS last_reply_at,
                   COALESCE(
                       json_agg(
                           json_build_object(
                               'reply_text', sr.reply_text,
                               'admin_name', sr.admin_name,
                               'created_at', sr.created_at
                           ) ORDER BY sr.created_at ASC
                       ) FILTER (WHERE sr.id IS NOT NULL),
                       '[]'::json
                   ) AS replies
            FROM email_log el
            LEFT JOIN org_info oi ON oi.org_id = el.org_id
            LEFT JOIN suggestion_replies sr ON sr.email_log_id = el.id
            GROUP BY el.id, el.org_id, el.date, el.subject, el."text", el.created_at, oi.org_name
            ORDER BY el.created_at DESC NULLS LAST
            """
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        result = []
        for row in rows:
            item = dict(zip(cols, row))
            item['content'] = item.get('text', '')
            item['created_at'] = item['created_at'].isoformat() if item.get('created_at') else ''
            item['date'] = str(item['date']) if item.get('date') else ''
            item['reply_count'] = item.get('reply_count', 0)
            for r in item.get('replies') or []:
                if r.get('created_at'):
                    r['created_at'] = str(r['created_at'])
            result.append(item)
        return result
    finally:
        cursor.close()
        return_connection(conn)
