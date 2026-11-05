"""Data lifecycle (WP6, planner D-14): staff soft delete -> retention purge, and PII anonymisation.

* `delete_staff` (core_functions/add_staff_function.py) is the SOFT delete: users.status = 2, the row and its history stay.
* `purge_deleted_staff` removes staff that have been in status 2 for more than N days (default 30): the staff row and
  everything tied to it are deleted exactly like `permanently_delete_staff` (both run the SAME step list below,
  `STAFF_STEPS`), except staff with PAYROLL HISTORY (a `salary_payments` or `payroll` row): their rows must stay, so the
  private data is removed and the rest is ANONYMISED in place (name -> 'Deleted user', placeholder mobile / e-mail, tokens
  cleared, bank / identity details blanked, photos removed). Each staff member is one transaction (all or nothing).
  S3 objects (legacy face data, profile photo, punch photos, chat files) are deleted AFTER the commit through
  `delete_s3_assets` (tests replace it), and the Rekognition faces + user_face_index rows through `remove_face_data`.
* `anonymise_account` is the account-deletion variant: every user of the organisations of the deleted administrator is
  anonymised in place (rows and references stay), customer contact data of the leads is blanked, the company rows are
  blanked. The caller runs it inside ONE transaction.

Everything takes a cursor; commit / rollback is the caller's (`purge_deleted_staff` commits per staff member through the
cursor's connection).
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from utils.timezone import get_utc_now

logger = logging.getLogger(__name__)

ANON_NAME = "Deleted user"
ANON_ORG_NAME = "Deleted company"
ANON_CONTACT_NAME = "Deleted contact"
DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650
DEFAULT_BATCH = 500

PRIVATE = "private"      # personal / operational data: always removed
HISTORY = "history"      # payroll and attendance history: removed on a full delete, kept when the row is anonymised

# (kind, sql): every placeholder is the staff member's user_id. The ORDER of the first 24 entries and their text are
# those of the former permanently_delete_staff (statements unchanged); two entries are new (user_push_tokens,
# face_attempt_log). Columns checked against punchapp_ddl_latest.sql.
STAFF_STEPS: Tuple[Tuple[str, str], ...] = (
    (HISTORY, "DELETE FROM leave_dates WHERE request_id IN (SELECT request_id FROM requests WHERE user_id = %s)"),
    (HISTORY, "DELETE FROM requests WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM chat_activity WHERE chat_id IN (SELECT chat_id FROM chat WHERE sender_id = %s OR receiver_id = %s)"),
    (PRIVATE, "DELETE FROM chat WHERE sender_id = %s OR receiver_id = %s"),
    (PRIVATE, "DELETE FROM group_members WHERE member_id = %s"),
    (PRIVATE, "DELETE FROM announcement_recipients WHERE user_id = %s"),
    (HISTORY, "DELETE FROM attendance WHERE user_id = %s"),
    (HISTORY, "DELETE FROM payroll WHERE user_id = %s"),
    (HISTORY, "DELETE FROM odometer_readings WHERE user_id = %s"),
    (HISTORY, "DELETE FROM travel_bill_expenses WHERE user_id = %s"),
    (HISTORY, "DELETE FROM advance_details WHERE user_id = %s"),
    (HISTORY, "DELETE FROM assigned_incentives WHERE user_id = %s"),
    (HISTORY, "DELETE FROM assigned_wages WHERE user_id = %s"),
    (HISTORY, "DELETE FROM daily_hourly_wages WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM manager_assignments WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM punchapp_manager_permissions WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM leads_assignment_info WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM staff_location_logs WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM activity_tracking WHERE user_id = %s"),
    (HISTORY, "DELETE FROM incentive_bonus_details WHERE user_id = %s"),
    (HISTORY, "DELETE FROM incentive_count_details WHERE user_id = %s"),
    (HISTORY, "DELETE FROM incentive_overtime_details WHERE user_id = %s"),
    (HISTORY, "DELETE FROM incentive_turnover_details WHERE user_id = %s"),
    (HISTORY, "DELETE FROM incentive_templates WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM user_push_tokens WHERE user_id = %s"),
    (PRIVATE, "DELETE FROM face_attempt_log WHERE user_id = %s"),
    (HISTORY, "DELETE FROM users_additional_info WHERE user_id = %s"),
)

_UNASSIGN_SQL = "UPDATE users SET manager_id = NULL WHERE manager_id = %s"
_DELETE_USER_SQL = "DELETE FROM users WHERE user_id = %s"


def _run(cursor, sql: str, user_id: str) -> None:
    cursor.execute(sql, (user_id,) * sql.count("%s"))


def anonymous_mobile(user_id: str) -> str:
    """Placeholder that cannot collide with a real number or with another anonymised user (users.mobile_number is
    NOT NULL varchar(255) and is what the login looks up)."""
    return "deleted-" + user_id


def anonymous_email(user_id: str) -> str:
    """Placeholder e-mail on the reserved `.invalid` domain, unique per user, within varchar(50)."""
    return "del-" + user_id[:36] + "@x.invalid"


# ------------------------------------------------------------------------------------------------ S3 side effects
def delete_s3_assets(owner_id: str, urls: Optional[List[str]] = None) -> dict:
    """Best-effort removal of the S3 objects of one user (face data, profile photo, punch photos, chat files). Never
    raises. `owner_id` may also be an organisation id (its logo keys are added by `delete_org_logo`)."""
    try:
        from ml.s3 import delete_employee_s3_assets
        try:
            from ml.s3 import HEIC_EXT
        except ImportError:
            HEIC_EXT = "heic"
        extra = list(urls or []) + [f"employee_profile/{owner_id}.{HEIC_EXT}"]
        return delete_employee_s3_assets(owner_id, extra_urls=extra)
    except Exception:
        logger.exception("S3 cleanup failed for %s", owner_id)
        return {"errors": ["s3 cleanup failed"]}


def delete_org_logo(org_id: str) -> dict:
    """Best-effort removal of the organisation logo objects. Never raises."""
    try:
        from ml.s3 import delete_employee_s3_assets
        try:
            from ml.s3 import HEIC_EXT
        except ImportError:
            HEIC_EXT = "heic"
        return delete_employee_s3_assets(org_id, extra_urls=[f"org_logo/{org_id}.png", f"org_logo/{org_id}.{HEIC_EXT}"])
    except Exception:
        logger.exception("logo cleanup failed for %s", org_id)
        return {"errors": ["logo cleanup failed"]}


def remove_face_data(user_id: str) -> dict:
    """Delete the user's faces from the Rekognition collection and their user_face_index rows (ml/face_service.remove).
    Best effort and never raises: a failure is logged and returned as {"ok": False, "removed": n, "errors": [...]}; the rows
    of faces that could not be deleted stay, so the cleanup can be retried. Call it AFTER the user's database rows were
    committed (it opens its own short transactions)."""
    try:
        from ml import face_service
        res = face_service.remove(user_id)
        out = {"ok": bool(res.ok), "removed": int(res.removed), "errors": list(res.errors)}
    except Exception as exc:
        logger.exception("face data removal failed for %s", user_id)
        out = {"ok": False, "removed": 0, "errors": ["%s: %s" % (type(exc).__name__, exc)]}
    if not out["ok"]:
        logger.error("face data of user %s was NOT fully deleted: %s", user_id, out["errors"])
    return out


# ------------------------------------------------------------------------------------------------ row operations
def harvest_s3_urls(cursor, user_id: str) -> List[str]:
    """Punch-photo and chat-file URLs of the user (read BEFORE the rows are deleted / blanked)."""
    urls: List[str] = []
    cursor.execute("SELECT punchinface, punchoutface FROM attendance WHERE user_id = %s", (user_id,))
    for row in cursor.fetchall():
        if row.get("punchinface"):
            urls.append(row["punchinface"])
        if row.get("punchoutface"):
            urls.append(row["punchoutface"])
    cursor.execute(
        "SELECT file_url FROM chat WHERE (sender_id = %s OR receiver_id = %s) AND file_url IS NOT NULL",
        (user_id, user_id),
    )
    for row in cursor.fetchall():
        if row.get("file_url"):
            urls.append(row["file_url"])
    return urls


def delete_staff_rows(cursor, user_id: str) -> None:
    """Full delete: every step of STAFF_STEPS, staff assigned to this user are un-assigned (manager_id NULL), then the
    users row. Same statements and order as the former permanently_delete_staff."""
    for _kind, sql in STAFF_STEPS:
        _run(cursor, sql, user_id)
    _run(cursor, _UNASSIGN_SQL, user_id)
    _run(cursor, _DELETE_USER_SQL, user_id)


def blank_additional_info(cursor, user_id: str, now: datetime) -> None:
    """Remove the personal / bank / identity details of users_additional_info but keep the row (shift id, join date)."""
    cursor.execute(
        """UPDATE users_additional_info SET acc_no = '', ifsc_code = '', bank_name = '', bank_branch = '', dob = NULL,
           gender = '', blood_group = NULL, address = '', emergency_contact_no = '', father_name = NULL,
           marital_status = NULL, aadhaar_no = NULL, pan_no = NULL, uan_no = NULL, pf_no = NULL, esi_no = NULL,
           updated_at = %s WHERE user_id = %s""",
        (now, user_id),
    )


def anonymise_user_row(cursor, user_id: str, now: datetime) -> None:
    """Anonymise the users row in place: status 2 (deleted: the login reports it), placeholder name / mobile / e-mail,
    device and push tokens cleared, attribution identifiers cleared, bearer tokens revoked (auth_epoch + 1). The row,
    its ids, role, branch, department, employee id and organisation stay so every reference keeps resolving."""
    cursor.execute(
        """UPDATE users SET name = %s, mobile_number = %s, email = %s, device_token = NULL,
           device_token_expires_at = NULL, push_token = '', meta_campaign_id = NULL, meta_adset_id = NULL,
           meta_ad_id = NULL, install_source = NULL, attribution_platform = NULL, status = 2,
           auth_epoch = COALESCE(auth_epoch, 0) + 1, updated_at = %s WHERE user_id = %s""",
        (ANON_NAME, anonymous_mobile(user_id), anonymous_email(user_id), now, user_id),
    )


def anonymise_staff_rows(cursor, user_id: str, now: datetime) -> List[str]:
    """In-place anonymisation: the PRIVATE steps are deleted, attendance photo references are removed, the additional
    info is blanked and the users row anonymised. Returns the S3 URLs harvested before the rows changed."""
    urls = harvest_s3_urls(cursor, user_id)
    for kind, sql in STAFF_STEPS:
        if kind == PRIVATE:
            _run(cursor, sql, user_id)
    cursor.execute("UPDATE attendance SET punchinface = NULL, punchoutface = NULL WHERE user_id = %s", (user_id,))
    blank_additional_info(cursor, user_id, now)
    anonymise_user_row(cursor, user_id, now)
    return urls


# ------------------------------------------------------------------------------------------------ retention purge
def _check_days(days) -> int:
    try:
        n = int(days)
    except (TypeError, ValueError):
        raise ValueError("days must be a whole number")
    if n < 1 or n > MAX_RETENTION_DAYS:
        raise ValueError("days must be between 1 and %d" % MAX_RETENTION_DAYS)
    return n


def _classify(cursor, target: dict) -> Tuple[str, Optional[str]]:
    """('delete' | 'anonymise' | 'skip', reason) for one purge candidate."""
    uid = target["user_id"]
    if target.get("role_id") == 2:
        # same protection as permanently_delete_staff: staff still reporting to a manager (legacy parent_id link)
        cursor.execute("SELECT COUNT(*) AS cnt FROM users WHERE parent_id = %s AND status IN (0, 1)", (uid,))
        row = cursor.fetchone() or {}
        if int(row.get("cnt") or 0) > 0:
            return "skip", "manager_has_subordinates"
    cursor.execute(
        """SELECT (EXISTS (SELECT 1 FROM salary_payments WHERE user_id = %s)
                   OR EXISTS (SELECT 1 FROM payroll WHERE user_id = %s)) AS has_history""",
        (uid, uid),
    )
    row = cursor.fetchone() or {}
    return ("anonymise", None) if row.get("has_history") else ("delete", None)


def purge_deleted_staff(cursor, days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = True, now: Optional[datetime] = None,
                        conn=None, limit: int = DEFAULT_BATCH) -> dict:
    """Retention purge of soft-deleted staff (users.status = 2, role 2 or 3, last changed more than `days` days ago,
    not yet anonymised). `dry_run=True` (the default) only counts: nothing is written, no S3 call. Otherwise every
    staff member is purged in its own transaction (commit on success, rollback and continue on failure) and the S3
    objects are deleted after the commit. At most `limit` staff are handled per call (oldest first); run again for more.

    Returns {dry_run, days, cutoff, eligible, delete, anonymise, skipped:[{user_id, reason}], purged:[{user_id, action}],
    failed:[user_id], face_cleanup_failed:[user_id]} (the last: purged staff whose Rekognition faces could not be deleted;
    their user_face_index rows are kept). Raises ValueError for a bad `days`."""
    days = _check_days(days)
    now = now or get_utc_now()
    cutoff = now - timedelta(days=days)
    conn = conn if conn is not None else getattr(cursor, "connection", None)
    cursor.execute(
        """SELECT user_id, role_id FROM users
           WHERE status = 2 AND role_id IN (2, 3) AND updated_at < %s AND name <> %s
           ORDER BY updated_at ASC LIMIT %s""",
        (cutoff, ANON_NAME, int(limit)),
    )
    targets = list(cursor.fetchall() or [])
    result = {"dry_run": bool(dry_run), "days": days, "cutoff": cutoff.isoformat(), "eligible": len(targets),
              "delete": 0, "anonymise": 0, "skipped": [], "purged": [], "failed": [], "face_cleanup_failed": []}

    for target in targets:
        uid = target["user_id"]
        try:
            action, reason = _classify(cursor, target)
        except Exception:
            logger.exception("purge: classification of %s failed", uid)
            result["failed"].append(uid)
            continue
        if action == "skip":
            result["skipped"].append({"user_id": uid, "reason": reason})
            continue
        result[action] += 1
        if dry_run:
            continue
        try:
            if action == "delete":
                urls = harvest_s3_urls(cursor, uid)
                delete_staff_rows(cursor, uid)
            else:
                urls = anonymise_staff_rows(cursor, uid, now)
            conn.commit()
        except Exception:
            logger.exception("purge: %s failed, rolled back", uid)
            try:
                conn.rollback()
            except Exception:
                logger.exception("purge: rollback failed")
            result[action] -= 1
            result["failed"].append(uid)
            continue
        delete_s3_assets(uid, urls)
        if not remove_face_data(uid).get("ok", True):
            result["face_cleanup_failed"].append(uid)       # rows kept in user_face_index: the next run / a retry cleans up
        result["purged"].append({"user_id": uid, "action": action})
    return result


# ------------------------------------------------------------------------------------------------ account deletion
def anonymise_account(cursor, owner_user_id: str, now: Optional[datetime] = None) -> Dict[str, list]:
    """Anonymise the account of an administrator and everything of its organisations, IN PLACE (rows and references
    stay; the caller commits ONE transaction around the call). Per organisation owned by `owner_user_id`
    (org_info.parent_id): every user of the organisation (staff, managers, including staff created under a manager by the
    old code path) is anonymised like a retained staff row, the leads' customer contact data is blanked, the company rows
    are blanked. Finally the administrator's own user and company rows are anonymised and the deletion request is
    closed. Billing records (orders, payments, invoices, subscriptions) are NOT touched.
    Returns {"org_ids": [...], "user_ids": [...], "s3": [(user_id, urls), ...]} for the post-commit S3 cleanup."""
    now = now or get_utc_now()
    cursor.execute("SELECT org_id FROM org_info WHERE parent_id = %s", (owner_user_id,))
    org_ids = [r["org_id"] for r in cursor.fetchall()]
    user_ids: List[str] = []
    s3: List[Tuple[str, List[str]]] = []

    for org_id in org_ids:
        cursor.execute(
            """SELECT user_id FROM users
               WHERE parent_id = %s OR parent_id IN (SELECT user_id FROM users WHERE parent_id = %s)""",
            (org_id, org_id),
        )
        for row in cursor.fetchall():
            uid = row["user_id"]
            if uid == owner_user_id or uid in user_ids:
                continue
            user_ids.append(uid)
            s3.append((uid, anonymise_staff_rows(cursor, uid, now)))
        cursor.execute(
            "UPDATE leads_info SET client_name = %s, mobile_number = NULL, client_address = '', remarks = NULL, "
            "updated_at = %s WHERE org_id = %s",
            (ANON_CONTACT_NAME, now, org_id),
        )
        cursor.execute(
            """UPDATE org_info SET org_name = %s, org_address = '', org_latitude = 0, org_longitude = 0, org_gst = NULL,
               state = NULL, district = NULL, pincode = NULL, updated_at = %s WHERE org_id = %s""",
            (ANON_ORG_NAME, now, org_id),
        )
        cursor.execute(
            """UPDATE company_additional_info SET pan_number = '', company_website = '', cin_number = '', tan_number = '',
               msme_number = '', business_category = '', gst_number = NULL, updated_at = %s WHERE org_id = %s""",
            (now, org_id),
        )

    # the administrator
    s3.append((owner_user_id, anonymise_staff_rows(cursor, owner_user_id, now)))
    user_ids.append(owner_user_id)
    cursor.execute(
        """UPDATE company_additional_info SET pan_number = '', company_website = '', cin_number = '', tan_number = '',
           msme_number = '', business_category = '', gst_number = NULL, updated_at = %s WHERE user_id = %s""",
        (now, owner_user_id),
    )
    cursor.execute(
        "UPDATE account_deletion_requests SET status = 'deleted', actioned_at = %s, org_name = %s, mobile_number = NULL, "
        "email = NULL WHERE user_id = %s",
        (now, ANON_ORG_NAME, owner_user_id),
    )
    return {"org_ids": org_ids, "user_ids": user_ids, "s3": s3}


def finish_account_cleanup(summary: Dict[str, list]) -> None:
    """After the account anonymisation committed: remove the S3 objects and the Rekognition faces (never raises). Users whose
    faces could not be deleted are listed in summary["face_cleanup_failed"] (their user_face_index rows are kept)."""
    failed = summary.setdefault("face_cleanup_failed", [])
    for uid, urls in summary.get("s3", []):
        delete_s3_assets(uid, urls)
        if not remove_face_data(uid).get("ok", True):
            failed.append(uid)
    for org_id in summary.get("org_ids", []):
        delete_org_logo(org_id)
