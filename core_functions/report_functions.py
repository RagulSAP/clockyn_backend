from fastapi import HTTPException
from core_functions.db_function import get_db_connection, return_connection
from psycopg2.extras import RealDictCursor
import calendar
from datetime import date, datetime, timedelta
from utils.timezone import get_utc_now, IST
import io
import logging
import urllib.request

from core_functions import attendance_rules as rules, payroll_engine, salary_engine
from core_functions.attendance_functions import _guarded

from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader


logger = logging.getLogger(__name__)

DAY_NAMES   = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
MONTH_NAMES = ["","January","February","March","April","May","June",
               "July","August","September","October","November","December"]

C_DARK   = colors.HexColor('#1e3a5f')
C_HEAD2  = colors.HexColor('#2d5986')
C_GREEN  = colors.HexColor('#16a34a')
C_RED    = colors.HexColor('#dc2626')
C_LGRAY  = colors.HexColor('#f8fafc')
C_MGRAY  = colors.HexColor('#e2e8f0')
C_DGRAY  = colors.HexColor('#64748b')
C_BORDER = colors.HexColor('#cbd5e1')
C_BLUE   = colors.HexColor('#dbeafe')


def _fmt_time_parts(ts):
    """Split a punch timestamp into (HH:MM, AM/PM) for the two-line punch-time
    cell — kept separate so the AM/PM suffix can use its own small font instead
    of being clipped off by _cell's width-based truncation in the narrow
    per-day columns."""
    if not ts or not hasattr(ts, 'strftime'):
        return "-", ""
    # The database session runs in UTC: print the IST wall-clock time of the punch.
    if isinstance(ts, datetime):
        ts = rules.to_utc(ts).astimezone(IST)
    return ts.strftime("%I:%M"), ts.strftime("%p")

def _hours_to_hhmm(dec):
    if dec is None: return "-"
    mins = int(round(float(dec) * 60))
    h, m = divmod(abs(mins), 60)
    sign = '-' if mins < 0 else ''
    return f"{sign}{h:02d}:{m:02d}"

def _diff_min(work_dec, shift_min):
    if work_dec is None or shift_min is None: return None
    return int(round(float(work_dec) * 60)) - shift_min

def _fmt_diff(d):
    if d is None: return "-"
    h, m = divmod(abs(d), 60)
    return f"{'+' if d >= 0 else '-'}{h:02d}:{m:02d}"

def _cell(c, x, y, w, h, bg, text="", fs=6, bold=False,
          align='center', tc=None, border=True):
    c.setFillColor(bg)
    c.setStrokeColor(C_BORDER)
    c.rect(x, y, w, h, fill=1, stroke=1 if border else 0)
    if text:
        c.setFillColor(tc or colors.black)
        font = 'Helvetica-Bold' if bold else 'Helvetica'
        c.setFont(font, fs)
        s = str(text)
        max_w = w - 3
        while c.stringWidth(s, font, fs) > max_w and len(s) > 1:
            s = s[:-1]
        ty = y + h * 0.28
        if align == 'center':
            c.drawCentredString(x + w / 2, ty, s)
        elif align == 'right':
            c.drawRightString(x + w - 2, ty, s)
        else:
            c.drawString(x + 2, ty, s)


def _cell_time(c, x, y, w, h, bg, time_str, period_str, tc=None):
    """Punch-time cell: HH:MM on one line, AM/PM on a smaller line below —
    both comfortably narrower than the column even at 31-day-month widths, so
    neither needs _cell's character-clipping truncation."""
    c.setFillColor(bg)
    c.setStrokeColor(C_BORDER)
    c.rect(x, y, w, h, fill=1, stroke=1)
    main_tc = tc or colors.black
    c.setFillColor(main_tc)
    c.setFont('Helvetica', 6.5)
    ty = y + (h * 0.55 if period_str else h * 0.28)
    c.drawCentredString(x + w / 2, ty, time_str)
    if period_str:
        c.setFillColor(C_DGRAY)
        c.setFont('Helvetica', 4.3)
        c.drawCentredString(x + w / 2, y + h * 0.10, period_str)


async def generate_monthly_attendance_pdf(user_id: str, month_str: str) -> bytes:
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid month format. Use YYYY-MM")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute("""
            SELECT u.name, u.emp_id, u.mobile_number,
                   COALESCE(d.dept_name, '-') AS dept_name,
                   u.parent_id, s.start_time, s.end_time
            FROM users u
            LEFT JOIN dept_info d ON u.dept_id = d.dept_id AND d.org_id = COALESCE(u.parent_id, u.org_id)
            LEFT JOIN users_additional_info uai ON u.user_id = uai.user_id
            LEFT JOIN shift s ON uai.shift_id = s.shift_id AND s.status = 1
            WHERE u.user_id = %s
        """, (user_id,))
        emp = cursor.fetchone()
        if not emp:
            raise HTTPException(status_code=404, detail="Employee not found")

        org_id = emp['parent_id']

        cursor.execute("""
            SELECT o.org_name, o.org_address,
                   u.mobile_number AS phone, u.email,
                   COALESCE(cai.company_website, '') AS website
            FROM org_info o
            LEFT JOIN users u ON o.parent_id = u.user_id
            LEFT JOIN company_additional_info cai ON cai.org_id = o.org_id
            WHERE o.org_id = %s
        """, (org_id,))
        company = cursor.fetchone()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")

        cursor.execute("""
            SELECT ps.pay_slip_name, ps.basic_pay, ps.hra,
                   ps.epf, ps.professional_tax, ps.health_insurance,
                   pr.pay_slip_id
            FROM payroll pr
            JOIN pay_slip ps ON pr.pay_slip_id = ps.pay_slip_id
            WHERE pr.user_id = %s
        """, (user_id,))
        payslip = cursor.fetchone()

        custom_earn, custom_ded = [], []
        if payslip:
            cursor.execute(
                "SELECT add_earnings_name AS name, add_earnings_amount AS amount "
                "FROM payslip_add_earnings WHERE pay_slip_id = %s",
                (payslip['pay_slip_id'],))
            custom_earn = cursor.fetchall()
            cursor.execute(
                "SELECT add_deduction_name AS name, add_deduction_amount AS amount "
                "FROM payslip_add_deduction WHERE pay_slip_id = %s",
                (payslip['pay_slip_id'],))
            custom_ded = cursor.fetchall()

        cursor.execute("""
            SELECT
                attendance_date,
                array_agg(punch_in ORDER BY punch_in ASC)  AS punch_ins,
                array_agg(punch_out ORDER BY punch_in ASC) AS punch_outs,
                SUM(work_hours)                            AS work_hours
            FROM attendance
            WHERE user_id = %s
              AND EXTRACT(YEAR FROM attendance_date) = %s
              AND EXTRACT(MONTH FROM attendance_date) = %s
            GROUP BY attendance_date
            ORDER BY attendance_date
        """, (user_id, year, month))
        att_map = {r['attendance_date']: r for r in cursor.fetchall()}

        # Day-type counters and the attendance-rule fine come from the shared payroll engine, so the PDF shows the
        # same figures as the salary breakdown. Optional: a failure keeps the PDF (counters then read "-").
        figures = _guarded(cursor, "sp_pdf_figures",
                           lambda: salary_engine.month_attendance_figures(org_id, user_id, year, month, cursor=cursor),
                           None)

    finally:
        cursor.close()
        return_connection(conn)

    # Fetch org logo from S3
    logo_image = None
    try:
        from ml.s3 import get_org_logo_url
        logo_url = get_org_logo_url(org_id)
        if logo_url:
            with urllib.request.urlopen(logo_url, timeout=5) as resp:
                logo_image = ImageReader(io.BytesIO(resp.read()))
    except Exception:
        logo_image = None
    shift_min, shift_label = None, "-"
    if emp['start_time'] and emp['end_time']:
        st, et = emp['start_time'], emp['end_time']
        td = (timedelta(hours=et.hour, minutes=et.minute) -
              timedelta(hours=st.hour, minutes=st.minute))
        if td.total_seconds() < 0:
            td += timedelta(days=1)
        shift_min   = int(td.total_seconds() / 60)
        shift_label = f"{st.strftime('%I:%M %p')} - {et.strftime('%I:%M %p')}"

    total_days   = calendar.monthrange(year, month)[1]
    month_label  = f"{MONTH_NAMES[month]} {year}"
    generated_on = get_utc_now().strftime("%d %b %Y, %I:%M %p")

    # Payslip data
    if payslip:
        earn_list = (
            [("Basic Pay", float(payslip['basic_pay'])),
             ("HRA",       float(payslip['hra']))] +
            [(e['name'], float(e['amount'])) for e in custom_earn]
        )
        ded_list = (
            [("EPF",              float(payslip['epf'])),
             ("Professional Tax", float(payslip['professional_tax'])),
             ("Health Insurance", float(payslip['health_insurance']))] +
            [(d['name'], float(d['amount'])) for d in custom_ded]
        )
        fine = 0.0
        if figures:
            fine = payroll_engine.fine_amount(float(payslip['basic_pay']), figures['penalty']['penalty_days'],
                                              figures['working_days'])
        if fine > 0:
            ded_list.append(("Late Fine", fine))
    else:
        earn_list, ded_list = [], []

    ps_rows_count  = 2 + len(earn_list) + len(ded_list) + 3
    sum_rows_count = 6
    bot_data_rows  = max(ps_rows_count, sum_rows_count)

    # ── Page ──────────────────────────────────────────────────────────────────
    PW, PH = landscape(A4)   # 841.89 x 595.27 pt
    M      = 14
    CW     = PW - 2 * M

    # ── Fixed heights ─────────────────────────────────────────────────────────
    TITLE_H    = 34
    FOOTER_H   = 12
    GAP_TITLE  = 30
    GAP_INFO   = 50
    GAP_TABLE  = 50

    # ── Info box dimensions ───────────────────────────────────────────────────
    INFO_HDR_H = 16
    INFO_RH    = 16
    INFO_H     = INFO_HDR_H + 5 * INFO_RH   # 84

    # ── Attendance table dimensions ───────────────────────────────────────────
    LABEL_W   = 42
    DAY_COL_W = (CW - LABEL_W) / total_days
    DATE_RH   = 25
    DAY_RH    = 15
    DATA_RH   = 17
    ATT_H     = DATE_RH + DAY_RH + 8 * DATA_RH   # 8 data rows

    # ── Bottom section dimensions ─────────────────────────────────────────────
    BOT_HDR   = 12
    BOT_RH    = 12
    BOT_FS    = 7

    # ── Derived positions (top-down) ──────────────────────────────────────────
    ATT_TOP   = PH - M - TITLE_H - GAP_TITLE - INFO_H - GAP_INFO
    BOT_START = ATT_TOP - ATT_H - GAP_TABLE
    BOT_END   = M + FOOTER_H

    # ── Canvas ────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    c   = pdfcanvas.Canvas(buf, pagesize=landscape(A4))
    c.setTitle(f"Attendance Summary - {month_label}")

    cur_y = PH - M - 20   # small top margin

    # ── Title ─────────────────────────────────────────────────────────────────
    c.setFillColor(C_DARK)
    c.rect(M, cur_y - TITLE_H, CW, TITLE_H, fill=1, stroke=0)
    LOGO_PAD  = 4
    LOGO_SIZE = TITLE_H - LOGO_PAD * 2
    if logo_image:
        c.drawImage(logo_image, M + LOGO_PAD, cur_y - TITLE_H + LOGO_PAD,
                    width=LOGO_SIZE, height=LOGO_SIZE, preserveAspectRatio=True, mask='auto')
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 13)
    c.drawCentredString(PW / 2, cur_y - TITLE_H + 20, 'MONTHLY ATTENDANCE SUMMARY')
    c.setFont('Helvetica', 10)
    c.setFillColor(colors.HexColor('#93c5fd'))
    c.drawCentredString(PW / 2, cur_y - TITLE_H + 8, month_label)
    cur_y -= TITLE_H + GAP_TITLE

    # ── Info boxes ────────────────────────────────────────────────────────────
    half_w = (CW - 2) / 2

    def draw_info_box(bx, by, bw, header, pairs):
        c.setFillColor(C_HEAD2)
        c.rect(bx, by - INFO_HDR_H, bw, INFO_HDR_H, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 7.5)
        c.drawString(bx + 4, by - INFO_HDR_H + 3, header)
        ry    = by - INFO_HDR_H
        lbl_w = bw * 0.32
        val_w = bw - lbl_w
        info_fs = min(8, max(6, INFO_RH * 0.55))
        for i, (lbl, val) in enumerate(pairs):
            bg = C_LGRAY if i % 2 == 0 else colors.white
            _cell(c, bx, ry - INFO_RH, lbl_w, INFO_RH,
                  bg, lbl, fs=info_fs, bold=True, align='left', tc=C_DGRAY)
            v = str(val) if val else '-'
            while c.stringWidth(v, 'Helvetica', info_fs) > val_w - 6 and len(v) > 1:
                v = v[:-1]
            _cell(c, bx + lbl_w, ry - INFO_RH, val_w, INFO_RH,
                  bg, v, fs=info_fs, align='left', tc=colors.black)
            ry -= INFO_RH

    draw_info_box(M, cur_y, half_w, "Company Details", [
        ("Name",    company['org_name']),
        ("Address", company['org_address']),
        ("Phone",   company['phone'] or '-'),
        ("Email",   company['email'] or '-'),
        ("Website", company['website'] or '-'),
    ])
    draw_info_box(M + half_w + 2, cur_y, half_w, "Staff Details", [
        ("Name",        emp['name']),
        ("Employee ID", emp['emp_id']),
        ("Department",  emp['dept_name']),
        ("Mobile",      emp['mobile_number']),
        ("Shift",       shift_label),
    ])
    cur_y -= INFO_H + GAP_INFO

    # ── Attendance table ──────────────────────────────────────────────────────
    row_labels  = ["", "", "Punch In 1", "Punch Out 1", "Punch In 2", "Punch Out 2", "Punch In 3", "Punch Out 3", "Total Hrs", "Difference"]
    row_heights = [DATE_RH, DAY_RH, DATA_RH, DATA_RH, DATA_RH, DATA_RH, DATA_RH, DATA_RH, DATA_RH, DATA_RH]

    row_y = []
    ry = ATT_TOP
    for rh in row_heights:
        row_y.append(ry)
        ry -= rh

    # Label column
    for ri, (lbl, rh) in enumerate(zip(row_labels, row_heights)):
        bg = C_DARK if ri < 2 else C_MGRAY
        _cell(c, M, row_y[ri] - rh, LABEL_W, rh,
              bg, lbl, fs=7, bold=True, align='left',
              tc=colors.white if ri < 2 else C_DARK)

    # Day columns
    present_days = 0
    for day in range(1, total_days + 1):
        d          = date(year, month, day)
        att        = att_map.get(d)
        cx         = M + LABEL_W + (day - 1) * DAY_COL_W
        is_weekend = d.weekday() == 6
        col_bg     = colors.HexColor('#fef3c7') if is_weekend else (C_LGRAY if day % 2 == 0 else colors.white)

        # Row 0: Date — month on top, day number on bottom
        _cell(c, cx, row_y[0] - DATE_RH, DAY_COL_W, DATE_RH, C_DARK)
        c.setFillColor(colors.white)
        c.setFont('Helvetica', 6.5)
        c.drawCentredString(cx + DAY_COL_W / 2, row_y[0] - DATE_RH + DATE_RH * 0.65, d.strftime('%b'))
        c.setFont('Helvetica-Bold', 7)
        c.drawCentredString(cx + DAY_COL_W / 2, row_y[0] - DATE_RH + DATE_RH * 0.22, f"{d.day:02d}")

        # Row 1: Day name
        day_tc = colors.HexColor('#fbbf24') if is_weekend else colors.HexColor('#93c5fd')
        _cell(c, cx, row_y[1] - DAY_RH, DAY_COL_W, DAY_RH,
              C_HEAD2, DAY_NAMES[d.weekday()], fs=7, bold=False, align='center', tc=day_tc)

        if att and att['punch_ins'] and any(p is not None for p in att['punch_ins']):
            present_days += 1
            punch_ins  = [p for p in att['punch_ins']  if p is not None]
            punch_outs = [p for p in att['punch_outs'] if p is not None]

            pi1 = _fmt_time_parts(punch_ins[0])  if len(punch_ins)  > 0 else ("-", "")
            po1 = _fmt_time_parts(punch_outs[0]) if len(punch_outs) > 0 else ("-", "")
            pi2 = _fmt_time_parts(punch_ins[1])  if len(punch_ins)  > 1 else ("-", "")
            po2 = _fmt_time_parts(punch_outs[1]) if len(punch_outs) > 1 else ("-", "")
            pi3 = _fmt_time_parts(punch_ins[2])  if len(punch_ins)  > 2 else ("-", "")
            po3 = _fmt_time_parts(punch_outs[2]) if len(punch_outs) > 2 else ("-", "")

            total = _hours_to_hhmm(att['work_hours'])
            dm    = _diff_min(att['work_hours'], shift_min)
            diff  = _fmt_diff(dm)
            diff_tc = C_GREEN if (dm is not None and dm >= 0) else C_RED

            time_cells  = [pi1, po1, pi2, po2, pi3, po3]
            sum_data    = [total, diff]
            sum_colors  = [colors.black, diff_tc]
            sum_bolds   = [False, dm is not None]
        else:
            time_cells  = [("-", "")] * 6
            sum_data    = ["-", "-"]
            sum_colors  = [C_DGRAY, C_DGRAY]
            sum_bolds   = [False, False]

        for ri, (time_str, period_str) in enumerate(time_cells):
            _cell_time(c, cx, row_y[ri + 2] - DATA_RH, DAY_COL_W, DATA_RH,
                       col_bg, time_str, period_str,
                       tc=colors.black if time_str != "-" else C_DGRAY)

        for ri, (val, tc, bold) in enumerate(zip(sum_data, sum_colors, sum_bolds)):
            _cell(c, cx, row_y[ri + 8] - DATA_RH, DAY_COL_W, DATA_RH,
                  col_bg, val, fs=7, bold=bold, align='center', tc=tc)

    absent_days = total_days - present_days
    if figures:      # same day-type counters as the salary engine / attendance calendar
        present_days = figures['present_days']
        absent_days = figures['absent_days']

    # ── Bottom section ────────────────────────────────────────────────────────
    cur_y = BOT_START

    SUM_PAD    = 90   # left padding for summary section
    PS_PAD     = 90   # right padding for payslip section
    sum_w = CW * 0.22
    ps_w  = CW * 0.30
    px    = M + CW - ps_w - PS_PAD

    # Summary header + rows
    _cell(c, M + SUM_PAD, cur_y - BOT_HDR, sum_w, BOT_HDR,
          C_HEAD2, "Summary", fs=7.5, bold=True, align='left', tc=colors.white, border=False)
    ps_title = f"Payslip \u2014 {payslip['pay_slip_name']}" if payslip else "Payslip"
    _cell(c, px, cur_y - BOT_HDR, ps_w, BOT_HDR,
          C_HEAD2, ps_title, fs=7.5, bold=True, align='left', tc=colors.white, border=False)
    cur_y -= BOT_HDR

    SUM_RH    = (ps_rows_count * BOT_RH) / 6
    sum_lbl_w = sum_w * 0.65
    sum_val_w = sum_w - sum_lbl_w
    sum_rows  = [
        ("Total Days",   str(total_days)),
        ("Present Days", str(present_days)),
        ("Absent Days",  str(absent_days)),
        ("Half Days",    str(figures['half_days']) if figures else "-"),
        ("Leaves",       str(figures['leave_days']) if figures else "-"),
        ("Holidays",     str(figures['holiday_days']) if figures else "-"),
    ]
    sy = cur_y
    for i, (lbl, val) in enumerate(sum_rows):
        bg = C_LGRAY if i % 2 == 0 else colors.white
        _cell(c, M + SUM_PAD,             sy - SUM_RH, sum_lbl_w, SUM_RH,
              bg, lbl, fs=BOT_FS, bold=True, align='left', tc=C_DGRAY, border=True)
        _cell(c, M + SUM_PAD + sum_lbl_w, sy - SUM_RH, sum_val_w, SUM_RH,
              bg, val, fs=BOT_FS, align='center', tc=colors.black)
        sy -= SUM_RH
    c.setStrokeColor(C_BORDER)
    c.setLineWidth(0.4)
    c.line(M + SUM_PAD, sy, M + SUM_PAD + sum_w, sy)

    # Payslip rows
    LBL_W = ps_w * 0.68
    VAL_W = ps_w - LBL_W
    VAL_X = px + LBL_W
    R_PAD = 4

    def _sep(py):
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.4)
        c.line(px, py, px + ps_w, py)

    def _vdiv(py):
        c.setStrokeColor(C_BORDER)
        c.setLineWidth(0.4)
        c.line(VAL_X, py, VAL_X, py - BOT_RH)

    def _ps_sub_hdr(py, lbl):
        c.setFillColor(C_MGRAY)
        c.rect(px, py - BOT_RH, ps_w, BOT_RH, fill=1, stroke=0)
        _sep(py)
        c.setFillColor(C_DARK)
        c.setFont('Helvetica-Bold', BOT_FS)
        c.drawString(px + 4, py - BOT_RH + BOT_RH * 0.3, lbl)

    def _ps_data_row(py, lbl, val, bg):
        c.setFillColor(bg)
        c.rect(px,    py - BOT_RH, LBL_W, BOT_RH, fill=1, stroke=0)
        c.rect(VAL_X, py - BOT_RH, VAL_W, BOT_RH, fill=1, stroke=0)
        _sep(py)
        _vdiv(py)
        c.setFillColor(colors.black)
        c.setFont('Helvetica', BOT_FS)
        c.drawString(px + 4,                     py - BOT_RH + BOT_RH * 0.3, lbl)
        c.drawRightString(VAL_X + VAL_W - R_PAD, py - BOT_RH + BOT_RH * 0.3, val)

    def _ps_total(py, lbl, val):
        c.setFillColor(C_BLUE)
        c.rect(px, py - BOT_RH, ps_w, BOT_RH, fill=1, stroke=0)
        _sep(py)
        c.setFillColor(C_DARK)
        c.setFont('Helvetica-Bold', BOT_FS)
        c.drawString(px + 4,              py - BOT_RH + BOT_RH * 0.3, lbl)
        c.drawRightString(px + ps_w - R_PAD, py - BOT_RH + BOT_RH * 0.3, val)

    if payslip:
        total_earn = sum(a for _, a in earn_list)
        total_ded  = sum(a for _, a in ded_list)
        net_pay    = total_earn - total_ded
        py = cur_y

        _ps_sub_hdr(py, 'Earnings');  py -= BOT_RH
        for idx, (lbl, amt) in enumerate(earn_list):
            _ps_data_row(py, lbl, f'Rs. {amt:,.0f}',
                         C_LGRAY if idx % 2 == 0 else colors.white)
            py -= BOT_RH
        _ps_total(py, 'Total Earnings', f'Rs. {total_earn:,.0f}');  py -= BOT_RH

        _ps_sub_hdr(py, 'Deductions');  py -= BOT_RH
        for idx, (lbl, amt) in enumerate(ded_list):
            _ps_data_row(py, lbl, f'Rs. {amt:,.0f}',
                         C_LGRAY if idx % 2 == 0 else colors.white)
            py -= BOT_RH
        _ps_total(py, 'Total Deductions', f'Rs. {total_ded:,.0f}');  py -= BOT_RH

        c.setFillColor(C_DARK)
        c.rect(px, py - BOT_RH, ps_w, BOT_RH, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 7.5)
        ty = py - BOT_RH + BOT_RH * 0.3
        c.drawString(px + 4,              ty, 'Net Pay')
        c.drawRightString(px + ps_w - R_PAD, ty, f'Rs. {net_pay:,.0f}')
        _sep(py - BOT_RH)
    else:
        c.setFillColor(C_LGRAY)
        c.rect(px, cur_y - BOT_RH * sum_rows_count,
               ps_w, BOT_RH * sum_rows_count, fill=1, stroke=0)
        c.setFillColor(C_DGRAY)
        c.setFont('Helvetica', 7)
        c.drawCentredString(px + ps_w / 2,
                            cur_y - BOT_RH * sum_rows_count / 2,
                            'No payslip assigned.')

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_line_y = M + FOOTER_H - 2
    footer_text_y = M + 3
    c.setStrokeColor(C_MGRAY)
    c.setLineWidth(0.4)
    c.line(M, footer_line_y, M + CW, footer_line_y)
    c.setFillColor(C_DGRAY)
    c.setFont('Helvetica', 6.5)
    c.drawRightString(M + CW, footer_text_y, f"Generated on: {generated_on}")

    c.showPage()
    c.save()
    return buf.getvalue()


async def get_staff_salary(user_id: str, year: int, month: int) -> dict:
    """Salary of one employee for a month: a one-employee wrapper of the shared salary engine
    (`salary_engine.compute_user_salary`), so it always equals the breakdown / monthly-list row of the same employee.
    Response shape: `{"success": True, "data": {... earnings / deductions / totals ...}}` (additive fields only)."""
    if not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail="month must be between 1 and 12")
    if not 2000 <= year <= 2100:
        raise HTTPException(status_code=422, detail="year must be between 2000 and 2100")
    try:
        row = salary_engine.compute_user_salary(user_id, year, month)
    except HTTPException:
        raise
    except Exception:
        logger.exception("staff salary failed for %s (%s-%s)", user_id, year, month)
        raise HTTPException(status_code=500, detail="Failed to fetch salary")
    return {"success": True, "data": row["data"]}
