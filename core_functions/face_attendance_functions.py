from fastapi import HTTPException, UploadFile
from fastapi.responses import JSONResponse
from datetime import datetime
from utils.timezone import get_utc_now, IST, UTC, convert_utc_to_ist, format_ist_time
import asyncio
import logging
import math
import time
from core_functions.face_metrics import FaceAttempt, run_inference
from core_functions.db_function import get_db_connection, return_connection
from core_functions.face_recognition import prepare_face, verify_face
from ml import face_service
from ml.face import reason_for
from psycopg2.extras import RealDictCursor
from ml.s3 import save_punch_image
from core_functions.auth_function import send_punch_in_out_notification
from core_functions import attendance_rules as rules
from core_functions.attendance_functions import shift_for

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

GENERIC_ERROR_MESSAGE = "Something went wrong. Please try again."


def _log_punch_location(cursor, user_id, org_id, latitude, longitude, when, label):
    """Insert the punch location into staff_location_logs inside a SAVEPOINT: a failed INSERT aborts the whole
    PostgreSQL transaction, so without the savepoint a location problem would also lose the attendance row."""
    cursor.execute("SAVEPOINT sp_punch_location")
    try:
        cursor.execute(
            "INSERT INTO staff_location_logs (user_id, org_id, location, date_time) VALUES (%s, %s, POINT(%s, %s), %s)",
            (user_id, org_id, longitude, latitude, when)
        )
    except Exception as e:
        # Don't fail the punch if location logging fails
        logger.warning("Failed to log %s location: %s", label, e)
        cursor.execute("ROLLBACK TO SAVEPOINT sp_punch_location")
        return
    cursor.execute("RELEASE SAVEPOINT sp_punch_location")


async def _notify_safely(**kwargs):
    """Run the punch notification; a failure here must never affect a committed punch."""
    try:
        await send_punch_in_out_notification(**kwargs)
    except Exception as e:
        logger.warning("punch notification failed: %s", e)


def _schedule_notification(**kwargs):
    try:
        asyncio.ensure_future(_notify_safely(**kwargs))
    except Exception as e:
        logger.warning("could not schedule punch notification: %s", e)


def _fail(code, message, **extra):
    """Failure body: unchanged `success`/`message` plus additive `code`."""
    body = {"success": False, "message": message, "code": code}
    body.update(extra)
    return body



def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
 
 
async def check_face_registered(user_id: str):
    """Check if user has registered face (any row in user_face_index for the Rekognition collection)"""
    try:
        loop = asyncio.get_running_loop()
        registered = await loop.run_in_executor(None, face_service.is_registered, user_id)
    except Exception as e:
        logger.exception("face registration check failed for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to check face registration")
    return {"registered": bool(registered)}


async def check_punch_location(user_id: str, latitude: float, longitude: float):
    """Check if user is within branch radius before face capture"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.is_field_staff, u.branch_id,
                   b.gps_location[0] AS branch_lng,
                   b.gps_location[1] AS branch_lat,
                   COALESCE(b.punch_radius, 100) AS punch_radius
            FROM users u
            LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = COALESCE(u.parent_id, u.org_id)
            WHERE u.user_id = %s
        """, (user_id,))
        user_info = cursor.fetchone()

        if not user_info:
            return {"success": False, "message": "User not found"}
        
        is_field_staff = bool(user_info.get('is_field_staff', 0))
        branch_lat = user_info.get('branch_lat')
        branch_lng = user_info.get('branch_lng')
        punch_radius = user_info.get('punch_radius', 100)
        
        # Field staff can punch from anywhere
        if is_field_staff:
            return {"success": True, "message": "Location check passed"}
        
        # Check if branch has GPS configured
        if not branch_lat or not branch_lng:
            return {"success": True, "message": "Location check passed"}
        
        # Calculate distance
        distance = _haversine_distance(latitude, longitude, float(branch_lat), float(branch_lng))
        
        if distance > punch_radius:
            return {
                "success": False,
                "message": f"You are {int(distance)}m away from your branch. Must be within {punch_radius}m to punch."
            }
        
        return {"success": True, "message": "Location check passed"}
        
    except Exception as e:
        return {"success": False, "message": f"Location check failed: {str(e)}"}
    finally:
        cursor.close()
        return_connection(conn)
 
 
async def register_face(user_id: str, org_id: str, faces: list[UploadFile]):
    """Register user face for first time"""
    # Failures keep the HTTP status and the string `detail`; the body gains `code` (+ `reason`).
    att = FaceAttempt("register", user_id, org_id)

    def _reject(status, detail, code, reason=None):
        att.emit(code)
        content = {"detail": detail, "code": code}
        if reason:
            content["reason"] = reason
        return JSONResponse(status_code=status, content=content)

    if len(faces) < 4:
        return _reject(400, "Please provide at least 4 face images", "insufficient_images")

    prepared = []
    face_bytes_list = []
    for face_file in faces:
        img_bytes = await face_file.read()
        face_bytes_list.append(img_bytes)
        att.img_bytes = len(img_bytes)
        t0 = time.perf_counter()
        # local decode + EXIF + quality + hard pre-check (no AWS call yet: a hopeless photo costs nothing)
        result = await run_inference(prepare_face, img_bytes, for_enroll=True)
        att.detect_ms = int((time.perf_counter() - t0) * 1000)
        att.set_quality(result.get("quality"))

        if not result["success"]:
            return _reject(
                400, result["message"],
                result.get("code") or "no_face", result.get("reason"),
            )

        prepared.append(result)

    try:
        t1 = time.perf_counter()
        # DetectFaces + IndexFaces per photo; every photo must be usable (a refused photo undoes the whole registration)
        reg = await run_inference(face_service.register, user_id, [p["jpeg"] for p in prepared])
        att.match_ms = int((time.perf_counter() - t1) * 1000)
    except Exception as e:
        logger.exception("face register failed for user %s: %s", user_id, e)
        return _reject(500, "Failed to register face. Please try again.", "server_error")

    if not reg.success:
        if reg.code in ("no_face", "image_unreadable"):
            index = reg.failed_index if reg.failed_index is not None and 0 <= reg.failed_index < len(prepared) else 0
            quality = prepared[index].get("quality") or {}
            att.set_quality(quality)
            reason = reg.reason or (reason_for(quality) if reg.code == "no_face" else "unknown")
            return _reject(400, reg.message or "No face detected", reg.code, reason)
        if reg.code == "unavailable":
            return _reject(503, reg.message, "server_error")
        return _reject(500, "Failed to register face. Please try again.", "server_error")

    # The faces are registered (and committed) at this point: a profile photo problem must not turn that into a failure.
    try:
        from ml.s3 import save_employee_profile
        save_employee_profile(user_id, face_bytes_list[0])
    except Exception as e:
        logger.warning("profile photo upload after face register failed for user %s: %s", user_id, e)
    att.emit("ok")
    return {"success": True, "message": "Face registered successfully", "code": "ok"}


async def warmup_user(user_id: str):
    """Called when the punch screen opens. Rekognition keeps the faces server side, so there is nothing to pre-load: this
    only reports whether the user has registered a face (same response shape as before; no error if unregistered)."""
    t0 = time.perf_counter()
    registered = False
    ok = True
    try:
        loop = asyncio.get_running_loop()
        registered = bool(await loop.run_in_executor(None, face_service.is_registered, user_id))
    except Exception as e:
        ok = False
        logger.warning("face warmup failed for user %s: %s", user_id, e)
    return {"success": ok, "registered": registered, "ms": int((time.perf_counter() - t0) * 1000)}
 
 
async def punch_with_face(user_id: str, org_id: str, face: UploadFile, action: str, latitude: float = None, longitude: float = None, force_accept: bool = False):
    """Punch in/out with face verification and location.

    Wrapper: runs the punch and emits one measurement line per attempt. Every failure
    body carries an additive `code`; success carries `code: "ok"`.
    """
    att = FaceAttempt(action, user_id, org_id, forced=force_accept)
    try:
        res = await _punch_core(att, user_id, org_id, face, action, latitude, longitude, force_accept)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("punch failed for user %s: %s", user_id, e)
        res = _fail("server_error", GENERIC_ERROR_MESSAGE)
    if isinstance(res, dict):
        res.setdefault("code", "ok" if res.get("success") else "server_error")
        att.emit(res["code"])
    return res


async def _punch_core(att, user_id: str, org_id: str, face: UploadFile, action: str, latitude: float = None, longitude: float = None, force_accept: bool = False):
    # Holiday check — only gates starting a NEW attendance session; an
    # already-open session can still be punched out normally even if the day
    # is later marked a holiday.
    if action == "punch_in":
        from core_functions.holiday_functions import check_holiday_block
        holiday_name = check_holiday_block(org_id, user_id)
        if holiday_name:
            return _fail(
                "holiday_blocked",
                f"Today is a holiday ({holiday_name}). Attendance is not required.",
                is_holiday=True,
            )

    try:
        # Check if face is registered (one indexed lookup in user_face_index)
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, face_service.is_registered, user_id):
            return _fail("not_registered", "Face not registered. Please register your face first.")

        img_bytes = await face.read()
        att.img_bytes = len(img_bytes)
        face_mismatch = False

        if force_accept:
            # Skip face verification — directly accept, flag for admin review
            face_mismatch = True
        else:
            # Local decode / EXIF / quality first (free), then the face service (DetectFaces + SearchFacesByImage); both run on
            # the bounded thread pool so the event loop stays responsive
            t0 = time.perf_counter()
            result = await run_inference(prepare_face, img_bytes)
            att.detect_ms = int((time.perf_counter() - t0) * 1000)
            att.set_quality(result.get("quality"))
            if not result["success"]:
                extra = {}
                code = result.get("code") or "no_face"
                if code in ("no_face", "image_unreadable"):
                    extra["reason"] = result.get("reason") or "unknown"
                return _fail(code, result["message"], **extra)
            t1 = time.perf_counter()
            verify_result = await run_inference(verify_face, result, user_id)
            att.match_ms = int((time.perf_counter() - t1) * 1000)
            att.score = verify_result.get("score")           # similarity / 100 (0..1), None when there was no candidate
            if not verify_result["match"]:
                vcode = verify_result.get("code")
                if vcode == "no_face":
                    # several faces / quality refused by the service: same code and reason vocabulary as a failed detection
                    return _fail("no_face", verify_result.get("message") or "No face detected",
                                 reason=reason_for(result.get("quality") or {}, verify_result.get("reason")))
                if vcode == "unavailable":
                    return _fail("server_error", verify_result.get("message") or GENERIC_ERROR_MESSAGE)
                if vcode == "server_error":
                    return _fail("server_error", GENERIC_ERROR_MESSAGE)
                if vcode == "spoof_detected":
                    return _fail("spoof_detected", verify_result.get("message") or "Spoof detected. Please use a real face.")
                return _fail("no_match", "Face not recognized")
       
        # ── Single DB connection: geofence check + attendance ──
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            # Fetch user's branch GPS + radius, field-staff flag and role
            cursor.execute("""
                SELECT u.is_field_staff, u.branch_id, u.role_id, u.org_id, u.parent_id,
                       b.gps_location[0] AS branch_lng,
                       b.gps_location[1] AS branch_lat,
                       COALESCE(b.punch_radius, 100) AS punch_radius
                FROM users u
                LEFT JOIN branch_info b ON u.branch_id = b.branch_id AND b.org_id = COALESCE(u.parent_id, u.org_id)
                WHERE u.user_id = %s
            """, (user_id,))
            user_info = cursor.fetchone()
            print(f"[Notify] user_info for {user_id}: role_id={user_info.get('role_id') if user_info else None}")

            if user_info:
                is_field_staff = bool(user_info.get('is_field_staff', 0))
                branch_lat = user_info.get('branch_lat')
                branch_lng = user_info.get('branch_lng')
                punch_radius = user_info.get('punch_radius', 100)

                # Only enforce geofence for non-field staff when branch GPS is set
                if not is_field_staff and branch_lat and branch_lng and latitude is not None and longitude is not None:
                    distance = _haversine_distance(latitude, longitude, float(branch_lat), float(branch_lng))
                    if distance > punch_radius:
                        return _fail(
                            "out_of_range",
                            f"You are {int(distance)}m away from your branch. Must be within {punch_radius}m to punch."
                        )
                elif not is_field_staff and branch_lat and branch_lng and (latitude is None or longitude is None):
                    return _fail("location_required", "Location access is required to punch in/out.")

            now = get_utc_now()
            # Business date (D-2): the IST calendar date of the punch, never the UTC or DB-session date.
            today = rules.ist_today(now)
            location_str = f"{latitude},{longitude}" if latitude and longitude else None

            if action == "punch_in":
                print(f"Punch-in request for user_id: {user_id}, org_id: {org_id}, latitude: {latitude}, longitude: {longitude}")
                # Serialize concurrent punch-ins of the same user via advisory lock (per user, not per date: an
                # overnight session and the first punch of the new date must not run in parallel)
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(%s))",
                    (user_id,)
                )
                # Check if already punched in (active session with no punch_out) —
                # scoped to the user only, not attendance_date, so an open overnight
                # session started on the previous calendar day is still detected.
                cursor.execute("""
                    SELECT id, punch_in FROM attendance
                    WHERE user_id = %s
                    AND punch_in IS NOT NULL AND punch_out IS NULL
                    ORDER BY punch_in DESC
                    LIMIT 1
                    FOR UPDATE
                """, (user_id,))
                open_session = cursor.fetchone()

                auto_closed_previous = False
                if open_session:
                    if not rules.is_stale_open(open_session['punch_in'], now):
                        return _fail("already_punched_in", "Already punched in. Please punch out first.")
                    # D-22: a session left open for more than 18 h is a forgotten punch-out. Close it at the shift
                    # end (punch-in + 9 h without a shift), flag it for admin review and carry on with this punch-in.
                    stale_in = rules.to_utc(open_session['punch_in'])
                    shift = shift_for(cursor, user_id, org_id, rules.ist_date_of(stale_in))
                    close_at = rules.auto_close_time(
                        stale_in,
                        shift['start_time'] if shift else None,
                        shift['end_time'] if shift else None,
                    )
                    cursor.execute("""
                        UPDATE attendance
                        SET punch_out = %s, work_hours = %s, auto_closed = TRUE, updated_at = %s
                        WHERE id = %s AND punch_out IS NULL
                    """, (close_at, rules.hours_between(stale_in, close_at), now, open_session['id']))
                    auto_closed_previous = True
                    logger.info("auto-closed stale session %s of user %s at %s", open_session['id'], user_id, close_at)

                # Check if already used 2 punch-in sessions today
                cursor.execute("""
                    SELECT COUNT(*) as cnt FROM attendance
                    WHERE user_id = %s AND attendance_date = %s
                """, (user_id, today))
                if cursor.fetchone()['cnt'] >= rules.MAX_SESSIONS_PER_DAY:
                    if auto_closed_previous:
                        conn.commit()  # keep the auto-close even though this punch-in is refused
                    return _fail("max_sessions", "Maximum 2 punch-in sessions allowed per day.")

                # Store location as POINT if coordinates provided
                location_point = None
                if latitude is not None and longitude is not None:
                    location_point = f"({longitude},{latitude})"

                    # Also save to staff_location_logs for all staff (punch-in location)
                    _log_punch_location(cursor, user_id, org_id, latitude, longitude, now, "punch-in")

                cursor.execute("""
                    INSERT INTO attendance (user_id, org_id, attendance_date, punch_in, punch_in_location, face_mismatch, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (user_id, org_id, today, now, location_point, face_mismatch, now, now))

                attendance_id = cursor.fetchone()['id']
                conn.commit()

                # Upload punch photo to S3 in background — don't block the response
                async def _save_punch_in_photo(aid=attendance_id, ib=img_bytes):
                    try:
                        loop = asyncio.get_running_loop()
                        url = await loop.run_in_executor(None, save_punch_image, user_id, org_id, aid, "punch_in", ib)
                        if url:
                            bg_conn = get_db_connection()
                            bg_cur = bg_conn.cursor()
                            try:
                                bg_cur.execute("UPDATE attendance SET punchinface = %s WHERE id = %s", (url, aid))
                                bg_conn.commit()
                            finally:
                                bg_cur.close()
                                return_connection(bg_conn)
                    except Exception as e:
                        print(f"Background punch-in photo upload failed: {e}")
                asyncio.ensure_future(_save_punch_in_photo())

                # Notify admin only when a staff/manager (role 2 or 3) punches in
                # (image_url is None: the photo is uploaded asynchronously and does not exist yet;
                # a notification problem must never turn this committed punch into a failure.)
                if user_info and user_info.get('role_id') in (2, 3):
                    try:
                        punch_time_ist = convert_utc_to_ist(now).strftime('%I:%M %p')
                        actual_org_id = user_info.get('parent_id') or org_id
                        logger.info("[Notify] Scheduling punch_in notification for user %s org %s", user_id, actual_org_id)
                        _schedule_notification(
                            user_id=user_id,
                            org_id=actual_org_id,
                            punch_type="punch_in",
                            punch_time=punch_time_ist,
                            image_url=None
                        )
                    except Exception as e:
                        logger.warning("punch_in notification setup failed: %s", e)

                response = {
                    "success": True,
                    "code": "ok",
                    "message": "Punched in successfully",
                    "attendance_id": attendance_id,
                    "time": convert_utc_to_ist(now).strftime("%I:%M %p"),
                    "face_mismatch": face_mismatch
                }
                if auto_closed_previous:
                    response["auto_closed_previous"] = True  # additive (D-22)
                return response

            elif action == "punch_out":
                print(f"Punch-out request for user_id: {user_id}, org_id: {org_id}, latitude: {latitude}, longitude: {longitude}")
                # Find the most recent active punch-in for this user — not scoped to
                # today's attendance_date, since an overnight shift's session was
                # opened under the *previous* calendar day and would otherwise never
                # be found once the date rolls over past midnight.
                cursor.execute("""
                    SELECT id, punch_in, attendance_date FROM attendance
                    WHERE user_id = %s
                    AND punch_out IS NULL
                    ORDER BY punch_in DESC LIMIT 1
                """, (user_id,))

                record = cursor.fetchone()
                if not record:
                    return _fail("no_active_punch", "No active punch in found")
                if record['punch_in'] is not None and rules.is_stale_open(record['punch_in'], now):
                    # Forgotten for more than 18 h (D-22): it is not an active session any more. The next
                    # punch-in closes it at the shift end; an admin can also fix it with an attendance edit.
                    return _fail("no_active_punch", "No active punch in found")

                # Calculate work hours for this session
                # DB stores UTC; if naive treat as UTC (never IST) for correct arithmetic
                punch_in_time = record['punch_in']
                if punch_in_time.tzinfo is None:
                    punch_in_time = punch_in_time.replace(tzinfo=UTC)
                work_hours = round((now - punch_in_time).total_seconds() / 3600, 2)

                # Calculate total work hours for the day (previous sessions + current) —
                # scoped to the session's own attendance_date (not "today"), so an
                # overnight session's total reflects sessions on the day it started.
                cursor.execute("""
                    SELECT COALESCE(SUM(work_hours), 0) as prev_hours FROM attendance
                    WHERE user_id = %s AND attendance_date = %s AND punch_out IS NOT NULL
                """, (user_id, record['attendance_date']))
                prev_hours = float(cursor.fetchone()['prev_hours'])
                total_work_hours = round(prev_hours + work_hours, 2)

                # Also save punch-out location to staff_location_logs if coordinates provided
                if latitude is not None and longitude is not None:
                    _log_punch_location(cursor, user_id, org_id, latitude, longitude, now, "punch-out")

                cursor.execute("""
                    UPDATE attendance
                    SET punch_out = %s, work_hours = %s, punch_out_location = POINT(%s, %s),
                        updated_at = %s, face_mismatch = %s
                    WHERE id = %s
                """, (now, work_hours, longitude, latitude, now, face_mismatch, record['id']))

                conn.commit()

                # Upload punch-out photo to S3 in background — don't block the response
                rec_id = record['id']
                async def _save_punch_out_photo(aid=rec_id, ib=img_bytes):
                    try:
                        loop = asyncio.get_running_loop()
                        url = await loop.run_in_executor(None, save_punch_image, user_id, org_id, aid, "punch_out", ib)
                        if url:
                            bg_conn = get_db_connection()
                            bg_cur = bg_conn.cursor()
                            try:
                                bg_cur.execute("UPDATE attendance SET punchoutface = %s WHERE id = %s", (url, aid))
                                bg_conn.commit()
                            finally:
                                bg_cur.close()
                                return_connection(bg_conn)
                    except Exception as e:
                        print(f"Background punch-out photo upload failed: {e}")
                asyncio.ensure_future(_save_punch_out_photo())

                punch_out_img_url = None  # URL saved in background; not needed for response

                # Notify admin only when a staff/manager (role 2 or 3) punches out
                if user_info and user_info.get('role_id') in (2, 3):
                    try:
                        punch_time_ist = convert_utc_to_ist(now).strftime('%I:%M %p')
                        actual_org_id = user_info.get('parent_id') or org_id
                        logger.info("[Notify] Scheduling punch_out notification for user %s org %s", user_id, actual_org_id)
                        _schedule_notification(
                            user_id=user_id,
                            org_id=actual_org_id,
                            punch_type="punch_out",
                            punch_time=punch_time_ist,
                            image_url=punch_out_img_url
                        )
                    except Exception as e:
                        logger.warning("punch_out notification setup failed: %s", e)

                return {
                    "success": True,
                    "code": "ok",
                    "message": "Punched out successfully",
                    "time": convert_utc_to_ist(now).strftime("%I:%M %p"),
                    "work_hours": work_hours,
                    "total_work_hours": total_work_hours,
                    "face_mismatch": face_mismatch
                }

            else:
                return _fail("invalid_action", "Invalid action")

        except Exception as e:
            conn.rollback()
            logger.exception("DB error during %s for user %s: %s", action, user_id, e)
            return _fail("server_error", GENERIC_ERROR_MESSAGE)
        finally:
            cursor.close()
            return_connection(conn)

    except Exception as e:
        logger.exception("Punch error for user %s: %s", user_id, e)
        return _fail("server_error", GENERIC_ERROR_MESSAGE)
 
 
async def get_today_attendance(user_id: str):
    """Today's (IST business date) attendance of the user - all punch sessions.

    An overnight session that started on an earlier date and is still open (and younger than the 18 h stale
    limit, D-22) is reported as today's open session until it is closed or auto-closed."""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        now = get_utc_now()
        today = rules.ist_today(now)
        cursor.execute("""
            SELECT id, punch_in, punch_out, work_hours, attendance_date, punchinface, punchoutface
            FROM attendance
            WHERE user_id = %s
              AND (attendance_date = %s
                   OR (punch_in IS NOT NULL AND punch_out IS NULL AND punch_in >= %s))
            ORDER BY punch_in ASC NULLS LAST
        """, (user_id, today, rules.stale_cutoff(now)))

        records = rules.select_today_sessions(cursor.fetchall(), today, now)

        if not records:
            return {
                "success": True,
                "date": str(today),
                "punch_in": None,
                "punch_out": None,
                "is_punched_in": False,
                "sessions": [],
                "punch_count": 0,
                "total_work_hours": 0
            }

        sessions = []
        total_hours = 0.0
        active_session = None

        for r in records:
            # DB stores UTC — convert to IST before formatting for display
            punch_in = format_ist_time(r['punch_in'], "%I:%M:%S %p") if r['punch_in'] else None
            punch_out = format_ist_time(r['punch_out'], "%I:%M:%S %p") if r['punch_out'] else None
            wh = float(r['work_hours']) if r['work_hours'] else None
            if wh:
                total_hours += wh
            sessions.append({"punch_in": punch_in, "punch_out": punch_out, "work_hours": wh})
            if r['punch_out'] is None:
                active_session = r

        # First punch_in and last punch_out for backward compatibility
        first_punch_in = format_ist_time(records[0]['punch_in'], "%I:%M:%S %p") if records[0]['punch_in'] else None
        last_punch_out_rec = next((r for r in reversed(records) if r['punch_out']), None)
        last_punch_out = format_ist_time(last_punch_out_rec['punch_out'], "%I:%M:%S %p") if last_punch_out_rec else None

        logger.info("Today's attendance for %s: %d session(s), is_punched_in=%s", user_id, len(records), active_session is not None)

        return {
            "success": True,
            "date": str(today),
            "punch_in": first_punch_in,
            "punch_out": last_punch_out,
            "is_punched_in": active_session is not None,
            "sessions": sessions,
            "punch_count": len(sessions),
            "total_work_hours": round(total_hours, 2)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("today attendance failed for %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to get attendance")
    finally:
        cursor.close()
        return_connection(conn)


async def get_attendance_sessions(user_id: str, date_str: str):
    """All punch sessions of a user on one business date (IST) with times in IST, hours and status."""
    try:
        target = datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")

    from ml.s3 import refresh_punch_image_url
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        now = get_utc_now()
        cursor.execute("""
            SELECT punch_in, punch_out, work_hours, punchinface, punchoutface, auto_closed
            FROM attendance
            WHERE user_id = %s AND attendance_date = %s
            ORDER BY punch_in ASC NULLS LAST, id ASC
        """, (user_id, target))
        sessions = []
        for r in cursor.fetchall():
            sessions.append({
                "punch_in": format_ist_time(r['punch_in'], "%I:%M:%S %p") if r['punch_in'] else None,
                "punch_out": format_ist_time(r['punch_out'], "%I:%M:%S %p") if r['punch_out'] else None,
                "work_hours": rules.session_hours(r['punch_in'], r['punch_out'], r['work_hours'], now),
                "status": rules.session_status(r['punch_out'], bool(r.get('auto_closed'))),
                "auto_closed": bool(r.get('auto_closed')),
                "punch_in_photo": refresh_punch_image_url(r['punchinface']),
                "punch_out_photo": refresh_punch_image_url(r['punchoutface'])
            })
        return {"success": True, "date": str(target), "sessions": sessions}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("attendance sessions failed for %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Failed to get attendance sessions")
    finally:
        cursor.close()
        return_connection(conn)
