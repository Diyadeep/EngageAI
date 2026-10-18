import re
import base64
from datetime import datetime, date, timedelta, timezone
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, make_response
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database import mysql

import cv2
import numpy as np
from ai.phone_detection import detect_phone

app = Flask(__name__)

# Validation Regex Patterns & Constants
EMAIL_REGEX = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
GMAIL_REGEX = r"^[A-Za-z0-9._%+-]+@gmail\.com$"
PHONE_REGEX = r"^[0-9]{10}$"
ALPHA_SPACE_REGEX = r"^[a-zA-Z\s]+$"
ROLL_NO_REGEX = r"^[a-zA-Z0-9\-\/]{3,20}$"
EMPLOYEE_ID_REGEX = r"^[a-zA-Z0-9\-\/]{3,20}$"
VALID_SEMESTERS = {
    "1", "2", "3", "4", "5", "6", "7", "8",
    "one", "two", "three", "four", "five", "six", "seven", "eight"
}

IST = timezone(timedelta(hours=5, minutes=30))

def get_now_ist():
    return datetime.now(IST)

def make_ist_datetime(date_obj, time_obj_or_delta):
    if isinstance(time_obj_or_delta, timedelta):
        total_seconds = int(time_obj_or_delta.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        t_obj = datetime.min.time().replace(hour=hours, minute=minutes, second=seconds)
    elif isinstance(time_obj_or_delta, datetime):
        t_obj = time_obj_or_delta.time()
    else:
        t_obj = time_obj_or_delta
    
    dt = datetime.combine(date_obj, t_obj)
    return dt.replace(tzinfo=IST)

TIME_12H_REGEX = r"^(0?[1-9]|1[0-2]):([0-5][0-9])(?::([0-5][0-9]))?\s*([AaPp][Mm])$"

def parse_time_input(time_str):
    if not time_str or not isinstance(time_str, str):
        return None
    time_str = time_str.strip()
    match = re.match(TIME_12H_REGEX, time_str)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3)) if match.group(3) else 0
    meridiem = match.group(4).upper()
    if meridiem == "AM":
        hour = 0 if hour == 12 else hour
    else:  # PM
        hour = 12 if hour == 12 else hour + 12
    from datetime import time
    return time(hour, minute, second)

def parse_date_input(date_str):
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]
    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
    return None

# -------------------------
# Configuration
# -------------------------
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.config["MYSQL_HOST"] = Config.MYSQL_HOST
app.config["MYSQL_USER"] = Config.MYSQL_USER
app.config["MYSQL_PASSWORD"] = Config.MYSQL_PASSWORD
app.config["MYSQL_DB"] = Config.MYSQL_DB

mysql.init_app(app)


# -------------------------
# Class Status Helpers & Synchronization
# -------------------------
def get_effective_class_status(c_date, s_time, e_time, status, now_ist=None):
    """
    Determines the correct class status among:
    - 'Scheduled': class date and start/end time are in future / not yet passed, not started.
    - 'In Progress': teacher started class and it is currently active.
    - 'Completed': teacher ended class or completion confirmed.
    - 'Not Taken': scheduled end time has passed and class was never started.
    """
    if now_ist is None:
        now_ist = get_now_ist()

    status_str = str(status).strip() if status else ""
    status_lower = status_str.lower()

    if status_lower == "completed":
        return "Completed"
    if status_lower in ("in progress", "active"):
        return "In Progress"
    if status_lower == "not taken":
        return "Not Taken"

    # For Scheduled or any unstarted state:
    end_dt = make_ist_datetime(c_date, e_time)
    if now_ist > end_dt:
        return "Not Taken"

    return "Scheduled"


def classify_class_status(c_date, s_time, e_time, status, now_ist=None):
    """Categorizes class session status for dashboard stat cards and metrics."""
    eff = get_effective_class_status(c_date, s_time, e_time, status, now_ist)
    if eff == "In Progress":
        return "active"
    elif eff == "Completed":
        return "completed"
    elif eff == "Not Taken":
        return "not_taken"
    else:
        return "upcoming"


def sync_class_session_statuses(cur=None):
    """
    Recalculates and persists class session statuses into the database:
    1. Past dates with status 'Scheduled' -> 'Not Taken'
    2. Today's classes whose end_time has passed with status 'Scheduled' -> 'Not Taken'
    3. Normalizes legacy 'Active' -> 'In Progress'
    """
    owns_cur = False
    if cur is None:
        cur = mysql.connection.cursor()
        owns_cur = True

    try:
        now_ist = get_now_ist()
        today_ist = now_ist.date()
        now_time = now_ist.time()

        # 1. Scheduled classes on past dates
        cur.execute(
            """
            UPDATE class_sessions
            SET status = 'Not Taken'
            WHERE status = 'Scheduled' AND class_date < %s
            """,
            (today_ist,)
        )

        # 2. Scheduled classes today whose end time has passed
        cur.execute(
            """
            UPDATE class_sessions
            SET status = 'Not Taken'
            WHERE status = 'Scheduled' AND class_date = %s AND end_time < %s
            """,
            (today_ist, now_time)
        )

        # 3. Normalize legacy 'Active' to 'In Progress'
        cur.execute(
            """
            UPDATE class_sessions
            SET status = 'In Progress'
            WHERE status = 'Active'
            """
        )

        if hasattr(cur, 'connection') and cur.connection:
            cur.connection.commit()
        else:
            mysql.connection.commit()
    except Exception as e:
        print("Error during sync_class_session_statuses:", e)
    finally:
        if owns_cur:
            cur.close()



# -------------------------
# Home Page
# -------------------------
@app.route("/")
def home():
    return render_template("index.html")


# -------------------------
# Login
# -------------------------
@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")

        # Backend Validation
        if not email or not password or not re.match(EMAIL_REGEX, email):
            flash("Invalid email or password")
            return redirect(url_for("login"))

        cur = mysql.connection.cursor()

        # Check Student Table
        cur.execute(
            "SELECT * FROM students WHERE email=%s AND password=%s",
            (email, password)
        )
        user = cur.fetchone()

        if user:
            session.pop("teacher", None)
            session.pop("admin", None)
            session["student"] = email
            cur.close()
            return redirect(url_for("student_dashboard"))

        # Check Teacher Table
        cur.execute(
            "SELECT * FROM teachers WHERE email=%s",
            (email,)
        )
        user = cur.fetchone()

        if user:
            if len(user) >= 7:
                stored_pw = user[6]
                is_valid = False
                if stored_pw == password:
                    is_valid = True
                else:
                    try:
                        is_valid = check_password_hash(stored_pw, password)
                    except Exception:
                        is_valid = False
                if not is_valid:
                    user = None

        if user:
            session.pop("student", None)
            session.pop("admin", None)
            session["teacher"] = email
            cur.close()
            return redirect(url_for("teacher_dashboard"))

        # Check Admin Table
        cur.execute(
            "SELECT * FROM admins WHERE email=%s AND password=%s",
            (email, password)
        )
        user = cur.fetchone()

        if user:
            session.pop("student", None)
            session.pop("teacher", None)
            session["admin"] = email
            cur.close()
            return redirect(url_for("admin_dashboard"))

        # Credentials did not match any of the three tables
        cur.close()
        flash("Invalid email or password")
        return redirect(url_for("login"))

    response = make_response(render_template("login.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# -------------------------
# Student Registration
# -------------------------
@app.route("/student-register", methods=["GET", "POST"])
def student_register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        roll_no = request.form.get("roll_no", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        course = request.form.get("course", "").strip()
        semester = request.form.get("semester", "").strip()
        password = request.form.get("password", "")

        # Backend Validation
        if not all([name, roll_no, email, phone, course, semester, password]):
            flash("All fields are required.")
            return redirect(url_for("student_register"))

        if not re.match(ALPHA_SPACE_REGEX, name):
            flash("Name must contain only alphabets and spaces.")
            return redirect(url_for("student_register"))

        if not re.match(ROLL_NO_REGEX, roll_no):
            flash("Please enter a valid Roll Number.")
            return redirect(url_for("student_register"))

        if not re.match(GMAIL_REGEX, email):
            flash("Please enter a valid Gmail address (example@gmail.com).")
            return redirect(url_for("student_register"))

        if not re.match(PHONE_REGEX, phone):
            flash("Phone number must contain exactly 10 digits.")
            return redirect(url_for("student_register"))

        if not re.match(ALPHA_SPACE_REGEX, course):
            flash("Course must contain only alphabets and spaces.")
            return redirect(url_for("student_register"))

        if semester.lower() not in VALID_SEMESTERS:
            flash("Semester must be a valid number from 1 to 8 or word (e.g. 1/ONE to 8/EIGHT).")
            return redirect(url_for("student_register"))

        if len(password) < 6:
            flash("Password must be at least 6 characters long.")
            return redirect(url_for("student_register"))

        cur = mysql.connection.cursor()

        # Duplicate Check for Email and Roll Number
        cur.execute(
            "SELECT email, roll_no FROM students WHERE LOWER(email) = %s OR LOWER(roll_no) = %s",
            (email, roll_no.lower())
        )
        existing = cur.fetchone()

        if existing:
            if existing[0].lower() == email:
                flash("❌ This email is already registered. Please use another email.")
            elif str(existing[1]).lower() == roll_no.lower():
                flash("Roll number is already registered.")
            cur.close()
            return redirect(url_for("student_register"))

        cur.execute("""
            INSERT INTO students
            (name, roll_no, email, phone, course, semester, password)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            name,
            roll_no,
            email,
            phone,
            course,
            semester,
            password
        ))

        mysql.connection.commit()
        cur.close()

        flash("Student registered successfully")
        return redirect(url_for("login"))

    response = make_response(render_template("student_register.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# -------------------------
# Teacher Registration
# -------------------------
@app.route("/teacher-register", methods=["GET", "POST"])
def teacher_register():

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        employee_id = request.form.get("employee_id", "").strip()
        department = request.form.get("department", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        password = request.form.get("password", "")

        # Backend Validation
        if not all([name, department, email, phone, password]):
            flash("All fields are required.")
            return redirect(url_for("teacher_register"))

        if not re.match(ALPHA_SPACE_REGEX, name):
            flash("Name must contain only alphabets and spaces.")
            return redirect(url_for("teacher_register"))

        if employee_id and not re.match(EMPLOYEE_ID_REGEX, employee_id):
            flash("Please enter a valid Employee ID.")
            return redirect(url_for("teacher_register"))

        if not re.match(ALPHA_SPACE_REGEX, department):
            flash("Department must contain only alphabets and spaces.")
            return redirect(url_for("teacher_register"))

        if not re.match(GMAIL_REGEX, email):
            flash("Please enter a valid Gmail address (example@gmail.com).")
            return redirect(url_for("teacher_register"))

        if not re.match(PHONE_REGEX, phone):
            flash("Phone number must contain exactly 10 digits.")
            return redirect(url_for("teacher_register"))

        if len(password) < 6:
            flash("Password must be at least 6 characters long.")
            return redirect(url_for("teacher_register"))

        cur = mysql.connection.cursor()

        # Duplicate Check for Email
        cur.execute(
            "SELECT email FROM teachers WHERE LOWER(email) = %s",
            (email,)
        )
        if cur.fetchone():
            flash("❌ This email is already registered. Please use another email.")
            cur.close()
            return redirect(url_for("teacher_register"))

        # If employee_id is not passed from form, auto-generate a unique ID (e.g. TCH003)
        if not employee_id:
            cur.execute("SELECT id FROM teachers ORDER BY id DESC LIMIT 1")
            last_row = cur.fetchone()
            try:
                next_num = (int(last_row[0]) + 1) if last_row and last_row[0] is not None else 1
            except (ValueError, TypeError):
                next_num = 1
            candidate_id = f"TCH{next_num:03d}"
            cur.execute("SELECT id FROM teachers WHERE employee_id = %s", (candidate_id,))
            while cur.fetchone():
                next_num += 1
                candidate_id = f"TCH{next_num:03d}"
            employee_id = candidate_id
        else:
            cur.execute("SELECT id FROM teachers WHERE LOWER(employee_id) = %s", (employee_id.lower(),))
            if cur.fetchone():
                flash("Employee ID is already registered.")
                cur.close()
                return redirect(url_for("teacher_register"))

        # Securely hash password before storing in MySQL
        hashed_password = generate_password_hash(password)

        cur.execute("""
            INSERT INTO teachers
            (name, employee_id, department, email, phone, password)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            name,
            employee_id,
            department,
            email,
            phone,
            hashed_password
        ))

        mysql.connection.commit()
        cur.close()

        flash("Teacher registered successfully")
        return redirect(url_for("login"))

    response = make_response(render_template("teacher_register.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# -------------------------
# Student Dashboard
# -------------------------
@app.route("/student-dashboard")
def student_dashboard():

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]

    cur = mysql.connection.cursor()

    # Get student details
    cur.execute(
        """
        SELECT id, name, roll_no, email, phone, course, semester
        FROM students
        WHERE email=%s
        """,
        (email,)
    )

    student = cur.fetchone()

    if not student:

        cur.close()

        session.pop("student", None)

        flash("Student account not found")

        return redirect(url_for("login"))

    # Sync class session statuses across database
    sync_class_session_statuses(cur)

    # Get latest engagement data
    cur.execute(
        """
        SELECT engagement_score,
               attendance_status,
               attention_level,
               recorded_at
        FROM engagement
        WHERE student_id=%s
        ORDER BY recorded_at DESC
        LIMIT 1
        """,
        (student[0],)
    )

    engagement = cur.fetchone()

    # Fetch class sessions for student's course & semester
    cur.execute(
        """
        SELECT cs.id, cs.subject, cs.course, cs.semester, cs.class_date,
               TIME_FORMAT(cs.start_time, '%%h:%%i %%p'),
               TIME_FORMAT(cs.end_time, '%%h:%%i %%p'),
               cs.status, t.name AS teacher_name, cs.start_time
        FROM class_sessions cs
        JOIN teachers t ON cs.teacher_id = t.id
        WHERE LOWER(cs.course) = LOWER(%s)
          AND LOWER(cs.semester) = LOWER(%s)
          AND cs.class_date >= CURRENT_DATE
          AND cs.status NOT IN ('Completed', 'Not Taken')
        ORDER BY cs.class_date ASC, cs.start_time ASC
        """,
        (student[5], student[6])
    )

    raw_classes = cur.fetchall()
    now_ist = get_now_ist()
    today_ist = now_ist.date()
    processed_classes = []

    for row in raw_classes:
        c_id, subject, course, semester, c_date, s_time_str, e_time_str, status, teacher_name, raw_start_time = row

        cur.execute("SELECT id, status, DATE_FORMAT(joined_at, '%%h:%%i %%p') FROM attendance WHERE class_id=%s AND student_id=%s", (c_id, student[0]))
        att_record = cur.fetchone()
        has_joined = bool(att_record)
        join_time_str = att_record[2] if att_record else None

        start_dt = make_ist_datetime(c_date, raw_start_time)
        start_window = start_dt - timedelta(hours=2)

        is_today = (c_date == today_ist)
        in_time_window = (now_ist >= start_window)
        can_join = is_today and in_time_window and (status.lower() not in ('completed', 'not taken'))

        processed_classes.append({
            'id': c_id,
            'subject': subject,
            'teacher': teacher_name,
            'course': course,
            'semester': semester,
            'class_date_str': c_date.strftime('%d/%m/%Y'),
            'start_time_str': s_time_str,
            'end_time_str': e_time_str,
            'status': status,
            'can_join': can_join,
            'has_joined': has_joined,
            'join_time_str': join_time_str,
            'is_today': is_today,
            'in_time_window': in_time_window
        })

    # Subject-wise & Overall Attendance and Engagement Calculation
    student_id = student[0]
    course = student[5]
    semester = student[6]

    cur.execute(
        """
        SELECT DISTINCT subject
        FROM class_sessions
        WHERE LOWER(course)=LOWER(%s) AND LOWER(semester)=LOWER(%s)
        ORDER BY subject ASC
        """,
        (course, semester)
    )
    subj_rows = cur.fetchall()
    subject_set = [row[0].strip() for row in subj_rows if row[0] and row[0].strip()]

    cur.execute(
        """
        SELECT DISTINCT cs.subject
        FROM attendance a
        JOIN class_sessions cs ON a.class_id = cs.id
        WHERE a.student_id = %s
        """,
        (student_id,)
    )
    att_subj_rows = cur.fetchall()
    for row in att_subj_rows:
        if row[0] and row[0].strip():
            s_name = row[0].strip()
            if not any(s.lower() == s_name.lower() for s in subject_set):
                subject_set.append(s_name)

    unique_subjects = []
    seen_lower = set()
    for s in subject_set:
        if s.lower() not in seen_lower:
            seen_lower.add(s.lower())
            unique_subjects.append(s)

    subject_data = []
    subject_attendance = []
    subject_engagement = []
    total_conducted_all = 0
    total_attended_all = 0

    for subj in unique_subjects:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM class_sessions
            WHERE LOWER(course)=LOWER(%s) AND LOWER(semester)=LOWER(%s) AND LOWER(subject)=LOWER(%s)
            """,
            (course, semester, subj)
        )
        total_cls = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COUNT(DISTINCT a.class_id)
            FROM attendance a
            JOIN class_sessions cs ON a.class_id = cs.id
            WHERE a.student_id=%s AND LOWER(cs.subject)=LOWER(%s) AND LOWER(a.status)='present'
            """,
            (student_id, subj)
        )
        att_cls = cur.fetchone()[0]

        total_conducted_all += total_cls
        total_attended_all += att_cls

        if total_cls > 0:
            att_val = (att_cls / total_cls) * 100.0
            att_val_rounded = round(att_val, 1)
            att_str = f"{int(att_val_rounded)}%" if att_val_rounded.is_integer() else f"{att_val_rounded}%"

            if att_val >= 90.0:
                att_badge = "Excellent"
                att_badge_type = "excellent"
            elif att_val >= 75.0:
                att_badge = "Good"
                att_badge_type = "good"
            elif att_val >= 50.0:
                att_badge = "Average"
                att_badge_type = "average"
            else:
                att_badge = "Low"
                att_badge_type = "low"
        else:
            att_val = None
            att_str = "No Data"
            att_badge = "No Data"
            att_badge_type = "nodata"

        cur.execute(
            """
            SELECT AVG(e.engagement_score)
            FROM engagement e
            JOIN class_sessions cs ON e.class_id = cs.id
            WHERE e.student_id=%s AND LOWER(cs.subject)=LOWER(%s)
            """,
            (student_id, subj)
        )
        eng_res = cur.fetchone()

        if eng_res and eng_res[0] is not None:
            eng_val = round(float(eng_res[0]), 1)
            eng_str = f"{int(eng_val)}%" if eng_val.is_integer() else f"{eng_val}%"

            if eng_val >= 75.0:
                eng_badge = "High"
                eng_badge_type = "high"
            elif eng_val >= 50.0:
                eng_badge = "Moderate"
                eng_badge_type = "moderate"
            else:
                eng_badge = "Low"
                eng_badge_type = "low"
        else:
            eng_val = None
            eng_str = "No Data"
            eng_badge = "No Data"
            eng_badge_type = "nodata"

        subject_data.append({
            'subject': subj,
            'classes_held': total_cls,
            'classes_attended': att_cls,
            'attendance_pct': att_val,
            'attendance_str': att_str,
            'attendance_badge': att_badge,
            'attendance_badge_type': att_badge_type,
            'engagement_pct': eng_val,
            'engagement_str': eng_str,
            'engagement_badge': eng_badge,
            'engagement_badge_type': eng_badge_type
        })

        subject_attendance.append({
            'subject': subj,
            'attended': att_cls,
            'total': total_cls,
            'percentage_str': att_str
        })

        subject_engagement.append({
            'subject': subj,
            'percentage_str': eng_str
        })

    if total_conducted_all > 0:
        overall_att_val = round((total_attended_all / total_conducted_all) * 100.0, 1)
        overall_attendance_str = f"{int(overall_att_val)}%" if overall_att_val.is_integer() else f"{overall_att_val}%"
    else:
        cur.execute("SELECT COUNT(DISTINCT class_id) FROM attendance WHERE student_id=%s AND LOWER(status)='present'", (student_id,))
        direct_att = cur.fetchone()[0]
        overall_attendance_str = "100%" if direct_att > 0 else "No Data"

    cur.execute(
        """
        SELECT AVG(engagement_score)
        FROM engagement
        WHERE student_id=%s
        """,
        (student_id,)
    )
    overall_eng_res = cur.fetchone()

    if overall_eng_res and overall_eng_res[0] is not None:
        overall_eng_val = round(float(overall_eng_res[0]), 1)
        overall_engagement_str = f"{int(overall_eng_val)}%" if overall_eng_val.is_integer() else f"{overall_eng_val}%"
    else:
        overall_engagement_str = "No Data"

    active_class_id = session.get("joined_class_id") or session.get("active_class_id")
    active_class_subject = session.get("joined_class_subject") or session.get("active_class_subject")

    if active_class_id:
        cur.execute("SELECT status FROM class_sessions WHERE id=%s", (active_class_id,))
        act_row = cur.fetchone()
        if not act_row or act_row[0].lower() in ['completed', 'not taken']:
            session.pop("joined_class_id", None)
            session.pop("active_class_id", None)
            session.pop("joined_class_subject", None)
            session.pop("active_class_subject", None)
            active_class_id = None
            active_class_subject = None

    cur.close()

    return render_template(
        "student/dashboard.html",
        student=student,
        engagement=engagement,
        active_classes=processed_classes,
        active_class_id=active_class_id,
        active_class_subject=active_class_subject,
        overall_attendance_str=overall_attendance_str,
        subject_attendance=subject_attendance,
        overall_engagement_str=overall_engagement_str,
        subject_engagement=subject_engagement,
        subject_data=subject_data
    )


# -------------------------
# Teacher Dashboard
# -------------------------
@app.route("/teacher-dashboard")
def teacher_dashboard():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    # Automatically synchronize class session statuses in database
    sync_class_session_statuses(cur)

    # Fetch teacher details
    cur.execute("SELECT id, name FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        session.pop("teacher", None)
        flash("Teacher account not found")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    # Fetch students
    cur.execute("SELECT * FROM students")
    students = cur.fetchall()

    # Fetch teacher's class sessions
    cur.execute(
        """
        SELECT id, subject, course, semester, class_date,
               TIME_FORMAT(start_time, '%%h:%%i %%p'),
               TIME_FORMAT(end_time, '%%h:%%i %%p'),
               status, created_at, start_time,
               DATE_FORMAT(class_date, '%%d/%%m/%%Y'),
               end_time
        FROM class_sessions
        WHERE teacher_id=%s
        ORDER BY created_at DESC
        """,
        (teacher_id,)
    )
    raw_classes = cur.fetchall()
    cur.close()

    now_ist = get_now_ist()
    today_ist = now_ist.date()
    processed_classes = []

    active_count = 0
    completed_count = 0
    upcoming_count = 0

    for row in raw_classes:
        c_id, subject, course, semester, c_date, s_time_str, e_time_str, raw_status, created_at, raw_start_time, date_str, raw_end_time = row
        start_dt = make_ist_datetime(c_date, raw_start_time)
        end_dt = make_ist_datetime(c_date, raw_end_time)
        start_window = start_dt - timedelta(hours=2)

        status = get_effective_class_status(c_date, raw_start_time, raw_end_time, raw_status, now_ist)

        if status == 'In Progress':
            active_count += 1
        elif status == 'Completed':
            completed_count += 1
        elif status == 'Scheduled':
            upcoming_count += 1

        is_today = (c_date == today_ist)
        # Class can be started: today, within start window (2h before until end time), and currently Scheduled
        can_start = is_today and (start_window <= now_ist <= end_dt) and (status == 'Scheduled')

        processed_classes.append({
            'id': c_id,
            'subject': subject,
            'course': course,
            'semester': semester,
            'class_date_str': date_str,
            'start_time_str': s_time_str,
            'end_time_str': e_time_str,
            'status': status,
            'can_start': can_start,
            'is_today': is_today
        })

    today_date = today_ist.strftime("%Y-%m-%d")
    max_date = (today_ist + timedelta(days=7)).strftime("%Y-%m-%d")
    total_classes_count = len(processed_classes)

    return render_template(
        "teacher/dashboard.html",
        teacher=teacher,
        students=students,
        classes=processed_classes,
        today_date=today_date,
        max_date=max_date,
        active_count=active_count,
        upcoming_count=upcoming_count,
        completed_count=completed_count,
        total_classes_count=total_classes_count
    )


# -------------------------
# Teacher - Create Class
# -------------------------
@app.route("/teacher/create-class", methods=["POST"])
def create_class():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    subject = request.form.get("subject", "").strip()
    course = request.form.get("course", "").strip()
    semester = request.form.get("semester", "").strip()
    class_date = request.form.get("class_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()

    # Validation
    if not all([subject, course, semester, class_date, start_time, end_time]):
        flash("All fields are required to create a class.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    TEXT_REGEX = r"^[a-zA-Z0-9\s\-\&\.\,]+$"
    if not re.match(TEXT_REGEX, subject):
        flash("Subject must contain valid text.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if not re.match(TEXT_REGEX, course):
        flash("Course must contain valid text.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if semester.lower() not in VALID_SEMESTERS:
        flash("Semester must be a valid number from 1 to 8 or word (e.g. 1/ONE to 8/EIGHT).")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    now_ist = get_now_ist()
    today = now_ist.date()
    max_date = today + timedelta(days=7)

    parsed_date = parse_date_input(class_date)
    if not parsed_date:
        flash("Invalid class date format. Please select a valid date.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if parsed_date < today:
        flash("Class date cannot be in the past.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if parsed_date > max_date:
        flash(f"Class date must be within 7 days from today (between {today.strftime('%d/%m/%Y')} and {max_date.strftime('%d/%m/%Y')}).")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    parsed_start = parse_time_input(start_time)
    if not parsed_start:
        flash("Invalid start time format. Please use 12-hour format (e.g. 09:30 AM).")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    parsed_end = parse_time_input(end_time)
    if not parsed_end:
        flash("Invalid end time format. Please use 12-hour format (e.g. 11:00 AM).")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    start_dt = datetime.combine(parsed_date, parsed_start).replace(tzinfo=IST)
    end_dt = datetime.combine(parsed_date, parsed_end).replace(tzinfo=IST)

    # Validate start time: if class date is today, start time must be strictly later than current time
    if parsed_date == today and start_dt <= now_ist:
        flash("Start time has already passed. Please select a future time.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    # Validate end time: end time must be later than start time
    if end_dt <= start_dt:
        flash("End time must be later than the start time.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        flash("Teacher account not found.")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    cur.execute(
        """
        INSERT INTO class_sessions
        (teacher_id, subject, course, semester, class_date, start_time, end_time, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'Scheduled')
        """,
        (teacher_id, subject, course, semester, parsed_date, parsed_start, parsed_end)
    )

    mysql.connection.commit()
    cur.close()

    flash("Class session created successfully!")
    return redirect(url_for("teacher_dashboard"))


# -------------------------
# Teacher - Start Class
# -------------------------
@app.route("/teacher/start-class/<int:class_id>", methods=["POST"])
def start_class(class_id):

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        flash("Teacher account not found.")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    cur.execute(
        """
        SELECT class_date, start_time, end_time, status
        FROM class_sessions
        WHERE id=%s AND teacher_id=%s
        """,
        (class_id, teacher_id)
    )
    cls = cur.fetchone()

    if not cls:
        cur.close()
        flash("Class session not found.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    class_date, start_time, end_time, status = cls
    now_ist = get_now_ist()
    today_ist = now_ist.date()

    if class_date != today_ist:
        cur.close()
        flash("A class can be started only on its scheduled date.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    start_dt = make_ist_datetime(class_date, start_time)
    end_dt = make_ist_datetime(class_date, end_time)
    start_window = start_dt - timedelta(hours=2)

    if now_ist < start_window:
        cur.close()
        flash("Class can be started 2 hours before the scheduled time.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if now_ist > end_dt or status.lower() == "not taken":
        cur.execute(
            "UPDATE class_sessions SET status='Not Taken' WHERE id=%s AND teacher_id=%s",
            (class_id, teacher_id)
        )
        mysql.connection.commit()
        cur.close()
        flash("This class session has expired (Not Taken) and cannot be started.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    if status.lower() == "completed":
        cur.close()
        flash("Class has already been completed.")
        return redirect(request.referrer or url_for("teacher_dashboard"))

    cur.execute(
        "UPDATE class_sessions SET status='In Progress' WHERE id=%s AND teacher_id=%s",
        (class_id, teacher_id)
    )
    mysql.connection.commit()
    cur.close()

    flash("Class session started successfully! Status updated to In Progress.")
    return redirect(request.referrer or url_for("teacher_dashboard"))


# -------------------------
# Teacher - End Class
# -------------------------
@app.route("/teacher/end-class/<int:class_id>", methods=["POST"])
def end_class(class_id):

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if teacher:
        teacher_id = teacher[0]
        cur.execute(
            "UPDATE class_sessions SET status='Completed' WHERE id=%s AND teacher_id=%s",
            (class_id, teacher_id)
        )
        mysql.connection.commit()
        flash("Class session ended successfully and marked as Completed.")
    cur.close()

    return redirect(request.referrer or url_for("teacher_dashboard"))


# -------------------------
# Student - Join Class & Record Attendance
# -------------------------
@app.route("/student/join-class/<int:class_id>", methods=["POST"])
def student_join_class(class_id):

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]
    cur = mysql.connection.cursor()

    cur.execute("SELECT id, course, semester FROM students WHERE email=%s", (email,))
    student = cur.fetchone()

    if not student:
        cur.close()
        flash("Student account not found.")
        return redirect(url_for("login"))

    student_id, student_course, student_semester = student

    cur.execute(
        "SELECT course, semester, class_date, start_time, status FROM class_sessions WHERE id=%s",
        (class_id,)
    )
    cls = cur.fetchone()

    if not cls:
        cur.close()
        flash("Class session not found.")
        return redirect(url_for("student_dashboard"))

    course, semester, class_date, start_time, status = cls
    now_ist = get_now_ist()
    today_ist = now_ist.date()

    if course.lower() != student_course.lower() or semester.lower() != student_semester.lower():
        cur.close()
        flash("You are not eligible for this class session.")
        return redirect(url_for("student_dashboard"))

    if class_date != today_ist:
        cur.close()
        flash("Class can only be joined on its scheduled date.")
        return redirect(url_for("student_dashboard"))

    if status.lower() in ("completed", "not taken"):
        cur.close()
        flash("Class session has ended or is not active. Joining is closed.")
        return redirect(url_for("student_dashboard"))

    start_dt = make_ist_datetime(class_date, start_time)
    start_window = start_dt - timedelta(hours=2)

    if now_ist < start_window:
        cur.close()
        flash("Class joining opens 2 hours before scheduled start time.")
        return redirect(url_for("student_dashboard"))

    cur.execute(
        """
        INSERT INTO attendance (class_id, student_id, status, joined_at)
        VALUES (%s, %s, 'PRESENT', NOW())
        ON DUPLICATE KEY UPDATE status='PRESENT'
        """,
        (class_id, student_id)
    )
    mysql.connection.commit()

    # Store joined class details in session for camera monitoring
    cur.execute("SELECT subject FROM class_sessions WHERE id=%s", (class_id,))
    subj_res = cur.fetchone()
    subject_name = subj_res[0] if subj_res else f"Class #{class_id}"
    cur.close()

    session["joined_class_id"] = class_id
    session["joined_class_subject"] = subject_name
    session["active_class_id"] = class_id
    session["active_class_subject"] = subject_name

    flash("Successfully joined class! Attendance marked as PRESENT.")
    return redirect(url_for("student_dashboard"))


# -------------------------
# Student Profile
# -------------------------
@app.route("/student/profile")
def student_profile():

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, roll_no, email, phone, course, semester FROM students WHERE email=%s", (email,))
    student = cur.fetchone()
    cur.close()

    if not student:
        session.pop("student", None)
        flash("Student account not found")
        return redirect(url_for("login"))

    return render_template("student/profile.html", student=student)


# -------------------------
# Student Engagement Page
# -------------------------
@app.route("/student/engagement")
def student_engagement():

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, roll_no, email, phone, course, semester FROM students WHERE email=%s", (email,))
    student = cur.fetchone()

    if not student:
        cur.close()
        session.pop("student", None)
        flash("Student account not found")
        return redirect(url_for("login"))

    student_id = student[0]
    course = student[5]
    semester = student[6]

    # Fetch distinct subjects for student's course & semester
    cur.execute(
        """
        SELECT DISTINCT subject 
        FROM class_sessions 
        WHERE LOWER(course)=LOWER(%s) AND LOWER(semester)=LOWER(%s)
        ORDER BY subject ASC
        """,
        (course, semester)
    )
    subj_rows = cur.fetchall()
    subjects = [row[0] for row in subj_rows]

    # Fallback to subjects from student's attended classes if course/sem didn't yield subjects
    if not subjects:
        cur.execute(
            """
            SELECT DISTINCT cs.subject
            FROM attendance a
            JOIN class_sessions cs ON a.class_id = cs.id
            WHERE a.student_id = %s
            ORDER BY cs.subject ASC
            """,
            (student_id,)
        )
        subj_rows = cur.fetchall()
        subjects = [row[0] for row in subj_rows]

    subject_engagement_list = []
    total_attended_classes_all = 0

    for subj in subjects:
        # Count classes attended by student for this subject
        cur.execute(
            """
            SELECT COUNT(DISTINCT a.class_id)
            FROM attendance a
            JOIN class_sessions cs ON a.class_id = cs.id
            WHERE a.student_id = %s 
              AND LOWER(cs.subject) = LOWER(%s)
              AND LOWER(a.status) = 'present'
            """,
            (student_id, subj)
        )
        attended_count = cur.fetchone()[0]
        total_attended_classes_all += attended_count

        # Average engagement score ONLY for attended classes of this subject
        avg_eng_score = None
        eng_score_int = None
        if attended_count > 0:
            cur.execute(
                """
                SELECT AVG(e.engagement_score)
                FROM engagement e
                JOIN attendance a ON e.student_id = a.student_id AND e.class_id = a.class_id
                JOIN class_sessions cs ON e.class_id = cs.id
                WHERE e.student_id = %s 
                  AND LOWER(cs.subject) = LOWER(%s)
                  AND LOWER(a.status) = 'present'
                """,
                (student_id, subj)
            )
            eng_res = cur.fetchone()
            if eng_res and eng_res[0] is not None:
                avg_eng_score = round(float(eng_res[0]), 1)
                eng_score_int = round(avg_eng_score)

        # Status determination based on calculated percentage (Requirement 5)
        if attended_count == 0:
            status_text = "No Classes Attended"
            status_badge = "neutral"
            pct_display = "N/A"
        elif eng_score_int is None:
            status_text = "No Data"
            status_badge = "neutral"
            pct_display = "N/A"
        elif eng_score_int >= 80:
            status_text = "Good"
            status_badge = "good"
            pct_display = f"{eng_score_int}%"
        elif eng_score_int >= 60:
            status_text = "Moderate"
            status_badge = "moderate"
            pct_display = f"{eng_score_int}%"
        else:
            status_text = "Needs Improvement"
            status_badge = "improvement"
            pct_display = f"{eng_score_int}%"

        # Fetch attended class session breakdown for this subject (Requirement 10)
        class_details = []
        if attended_count > 0:
            cur.execute(
                """
                SELECT cs.id,
                       DATE_FORMAT(cs.class_date, '%%d/%%m/%%Y') AS date_str,
                       TIME_FORMAT(cs.start_time, '%%h:%%i %%p') AS start_str,
                       TIME_FORMAT(cs.end_time, '%%h:%%i %%p') AS end_str,
                       a.status AS att_status,
                       DATE_FORMAT(a.joined_at, '%%d/%%m/%%Y %%h:%%i %%p') AS joined_str
                FROM attendance a
                JOIN class_sessions cs ON a.class_id = cs.id
                WHERE a.student_id = %s 
                  AND LOWER(cs.subject) = LOWER(%s)
                  AND LOWER(a.status) = 'present'
                ORDER BY cs.class_date DESC, cs.start_time DESC
                """,
                (student_id, subj)
            )
            att_classes = cur.fetchall()

            for ac in att_classes:
                c_id, date_str, start_str, end_str, att_status, joined_str = ac

                cur.execute(
                    "SELECT AVG(engagement_score) FROM engagement WHERE student_id=%s AND class_id=%s",
                    (student_id, c_id)
                )
                c_eng_res = cur.fetchone()
                if c_eng_res and c_eng_res[0] is not None:
                    c_eng_pct = f"{round(float(c_eng_res[0]))}%"
                    c_eng_val = round(float(c_eng_res[0]))
                else:
                    c_eng_pct = "N/A"
                    c_eng_val = None

                class_details.append({
                    'class_id': c_id,
                    'class_date_str': date_str,
                    'time_str': f"{start_str} - {end_str}",
                    'attendance_status': 'Present',
                    'joined_at_str': joined_str,
                    'engagement_pct': c_eng_pct,
                    'engagement_val': c_eng_val
                })

        subject_engagement_list.append({
            'subject': subj,
            'attended_count': attended_count,
            'avg_engagement_val': eng_score_int,
            'percentage_str': pct_display,
            'status_text': status_text,
            'status_badge': status_badge,
            'class_details': class_details
        })

    # Overall Engagement calculation: weighted average across all attended classes
    cur.execute(
        """
        SELECT AVG(e.engagement_score)
        FROM engagement e
        JOIN attendance a ON e.student_id = a.student_id AND e.class_id = a.class_id
        WHERE e.student_id = %s AND LOWER(a.status) = 'present'
        """,
        (student_id,)
    )
    overall_res = cur.fetchone()

    if overall_res and overall_res[0] is not None:
        overall_val = round(float(overall_res[0]))
        overall_engagement_str = f"{overall_val}%"
        if overall_val >= 80:
            overall_status = "Good"
        elif overall_val >= 60:
            overall_status = "Moderate"
        else:
            overall_status = "Needs Improvement"
    else:
        valid_scores = [se['avg_engagement_val'] for se in subject_engagement_list if se['avg_engagement_val'] is not None]
        if valid_scores:
            overall_val = round(sum(valid_scores) / len(valid_scores))
            overall_engagement_str = f"{overall_val}%"
            overall_status = "Good" if overall_val >= 80 else ("Moderate" if overall_val >= 60 else "Needs Improvement")
        else:
            overall_engagement_str = "No data available"
            overall_status = "N/A"

    cur.close()

    return render_template(
        "student/engagement.html",
        student=student,
        subject_engagement=subject_engagement_list,
        overall_engagement_str=overall_engagement_str,
        overall_status=overall_status,
        total_subjects=len(subjects),
        total_attended_classes_all=total_attended_classes_all
    )


# -------------------------
# Student Attendance Page
# -------------------------
@app.route("/student/attendance")
def student_attendance():

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, roll_no, email, phone, course, semester FROM students WHERE email=%s", (email,))
    student = cur.fetchone()

    if not student:
        cur.close()
        session.pop("student", None)
        flash("Student account not found")
        return redirect(url_for("login"))

    student_id = student[0]

    cur.execute(
        """
        SELECT a.id, cs.subject, t.name AS teacher_name, cs.course, cs.semester,
               DATE_FORMAT(cs.class_date, '%%d/%%m/%%Y'),
               TIME_FORMAT(cs.start_time, '%%h:%%i %%p'),
               TIME_FORMAT(cs.end_time, '%%h:%%i %%p'),
               a.status,
               DATE_FORMAT(a.joined_at, '%%h:%%i %%p')
        FROM attendance a
        JOIN class_sessions cs ON a.class_id = cs.id
        JOIN teachers t ON cs.teacher_id = t.id
        WHERE a.student_id=%s
        ORDER BY a.joined_at DESC
        """,
        (student_id,)
    )
    records = cur.fetchall()
    cur.close()

    return render_template(
        "student/attendance.html",
        student=student,
        records=records
    )


# -------------------------
# Student Reports Page
# -------------------------
@app.route("/student/reports")
def student_reports():

    if "student" not in session:
        return redirect(url_for("login"))

    email = session["student"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, roll_no, email, phone, course, semester FROM students WHERE email=%s", (email,))
    student = cur.fetchone()

    if not student:
        cur.close()
        session.pop("student", None)
        flash("Student account not found")
        return redirect(url_for("login"))

    student_id = student[0]

    # Retrieve unique subjects belonging ONLY to reports of this logged-in student
    cur.execute(
        """
        SELECT DISTINCT cs.subject
        FROM reports r
        JOIN class_sessions cs ON r.class_id = cs.id
        WHERE r.student_id = %s AND cs.subject IS NOT NULL AND TRIM(cs.subject) != ''
        ORDER BY cs.subject ASC
        """,
        (student_id,)
    )
    subj_rows = cur.fetchall()
    seen_subjs = set()
    subjects = []
    for row in subj_rows:
        if row[0]:
            s_val = row[0].strip()
            if s_val and s_val.lower() not in seen_subjs:
                seen_subjs.add(s_val.lower())
                subjects.append(s_val)

    selected_subject = request.args.get("subject", "").strip()
    reports = []

    if selected_subject:
        # First attempt exact case-insensitive match
        cur.execute(
            """
            SELECT r.id, r.report_title, r.summary, r.attendance_percentage, r.avg_engagement,
                   DATE_FORMAT(r.created_at, '%%d/%%m/%%Y %%h:%%i %%p') AS created_at_str,
                   t.name AS teacher_name, cs.subject,
                   DATE_FORMAT(cs.class_date, '%%d/%%m/%%Y') AS class_date_str,
                   TIME_FORMAT(cs.start_time, '%%h:%%i %%p') AS start_time_str,
                   TIME_FORMAT(cs.end_time, '%%h:%%i %%p') AS end_time_str,
                   r.attention_level, r.phone_info, r.overall_status,
                   s.name AS student_name, s.roll_no, s.course, s.semester
            FROM reports r
            JOIN teachers t ON r.teacher_id = t.id
            JOIN class_sessions cs ON r.class_id = cs.id
            JOIN students s ON r.student_id = s.id
            WHERE r.student_id = %s AND LOWER(cs.subject) = LOWER(%s)
            ORDER BY r.created_at DESC
            """,
            (student_id, selected_subject)
        )
        raw_reports = cur.fetchall()

        # If 0 results, try partial/substring match for search terms like 'math' or 'py'
        if not raw_reports:
            cur.execute(
                """
                SELECT r.id, r.report_title, r.summary, r.attendance_percentage, r.avg_engagement,
                       DATE_FORMAT(r.created_at, '%%d/%%m/%%Y %%h:%%i %%p') AS created_at_str,
                       t.name AS teacher_name, cs.subject,
                       DATE_FORMAT(cs.class_date, '%%d/%%m/%%Y') AS class_date_str,
                       TIME_FORMAT(cs.start_time, '%%h:%%i %%p') AS start_time_str,
                       TIME_FORMAT(cs.end_time, '%%h:%%i %%p') AS end_time_str,
                       r.attention_level, r.phone_info, r.overall_status,
                       s.name AS student_name, s.roll_no, s.course, s.semester
                FROM reports r
                JOIN teachers t ON r.teacher_id = t.id
                JOIN class_sessions cs ON r.class_id = cs.id
                JOIN students s ON r.student_id = s.id
                WHERE r.student_id = %s AND LOWER(cs.subject) LIKE LOWER(%s)
                ORDER BY r.created_at DESC
                """,
                (student_id, f"%{selected_subject}%")
            )
            raw_reports = cur.fetchall()

        for r in raw_reports:
            reports.append({
                'id': r[0],
                'report_title': r[1],
                'summary': r[2],
                'attendance_percentage': round(float(r[3]), 1) if r[3] is not None else 0.0,
                'avg_engagement': round(float(r[4]), 1) if r[4] is not None else 0.0,
                'created_at_str': r[5],
                'teacher_name': r[6],
                'subject': r[7],
                'class_date_str': r[8],
                'start_time_str': r[9],
                'end_time_str': r[10],
                'attention_level': r[11],
                'phone_info': r[12],
                'overall_status': r[13],
                'student_name': r[14],
                'roll_no': r[15],
                'course': r[16],
                'semester': r[17]
            })

    cur.close()

    return render_template(
        "student/reports.html",
        student=student,
        subjects=subjects,
        selected_subject=selected_subject,
        reports=reports
    )



# -------------------------
# Teacher - Profile Page
# -------------------------
@app.route("/teacher/profile")
def teacher_profile():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, employee_id, department, email, phone FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()
    cur.close()

    if not teacher:
        session.pop("teacher", None)
        flash("Teacher account not found")
        return redirect(url_for("login"))

    return render_template("teacher/profile.html", teacher=teacher)


# -------------------------
# Teacher - Classes Page
# -------------------------
@app.route("/teacher/classes")
def teacher_classes():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    # Automatically synchronize class session statuses in database
    sync_class_session_statuses(cur)

    cur.execute("SELECT id, name FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        session.pop("teacher", None)
        flash("Teacher account not found")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    cur.execute(
        """
        SELECT id, subject, course, semester, class_date,
               TIME_FORMAT(start_time, '%%h:%%i %%p'),
               TIME_FORMAT(end_time, '%%h:%%i %%p'),
               status, created_at, start_time,
               DATE_FORMAT(class_date, '%%d/%%m/%%Y'),
               end_time
        FROM class_sessions
        WHERE teacher_id=%s
        ORDER BY created_at DESC
        """,
        (teacher_id,)
    )
    raw_classes = cur.fetchall()
    cur.close()

    now_ist = get_now_ist()
    today_ist = now_ist.date()
    processed_classes = []

    for row in raw_classes:
        c_id, subject, course, semester, c_date, s_time_str, e_time_str, raw_status, created_at, raw_start_time, date_str, raw_end_time = row
        start_dt = make_ist_datetime(c_date, raw_start_time)
        end_dt = make_ist_datetime(c_date, raw_end_time)
        start_window = start_dt - timedelta(hours=2)

        status = get_effective_class_status(c_date, raw_start_time, raw_end_time, raw_status, now_ist)
        is_today = (c_date == today_ist)
        can_start = is_today and (start_window <= now_ist <= end_dt) and (status == 'Scheduled')

        processed_classes.append({
            'id': c_id,
            'subject': subject,
            'course': course,
            'semester': semester,
            'class_date_str': date_str,
            'start_time_str': s_time_str,
            'end_time_str': e_time_str,
            'status': status,
            'can_start': can_start,
            'is_today': is_today
        })

    today_date = today_ist.strftime("%Y-%m-%d")
    max_date = (today_ist + timedelta(days=7)).strftime("%Y-%m-%d")

    return render_template(
        "teacher/classes.html",
        teacher=teacher,
        classes=processed_classes,
        today_date=today_date,
        max_date=max_date
    )


# -------------------------
# Teacher - Attendance Page
# -------------------------
@app.route("/teacher/attendance")
def teacher_attendance():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        session.pop("teacher", None)
        flash("Teacher account not found")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    cur.execute(
        """
        SELECT a.id, s.name, s.roll_no, s.course, s.semester, cs.subject,
               DATE_FORMAT(cs.class_date, '%%d/%%m/%%Y'),
               TIME_FORMAT(cs.start_time, '%%h:%%i %%p'),
               TIME_FORMAT(cs.end_time, '%%h:%%i %%p'),
               a.status,
               DATE_FORMAT(a.joined_at, '%%h:%%i %%p')
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN class_sessions cs ON a.class_id = cs.id
        WHERE cs.teacher_id=%s
        ORDER BY a.joined_at DESC
        """,
        (teacher_id,)
    )
    records = cur.fetchall()
    cur.close()

    return render_template(
        "teacher/attendance.html",
        teacher=teacher,
        records=records
    )


# -------------------------
# Teacher - Reports Page
# -------------------------
@app.route("/teacher/reports")
def teacher_reports():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    cur = mysql.connection.cursor()

    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        session.pop("teacher", None)
        flash("Teacher account not found")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    # Fetch teacher's classes for dropdown
    cur.execute(
        """
        SELECT id, subject, course, semester, DATE_FORMAT(class_date, '%%d/%%m/%%Y')
        FROM class_sessions
        WHERE teacher_id=%s
        ORDER BY created_at DESC
        """,
        (teacher_id,)
    )
    raw_classes = cur.fetchall()
    classes = []
    for c in raw_classes:
        classes.append({
            'id': c[0],
            'subject': c[1],
            'course': c[2],
            'semester': c[3],
            'class_date_str': c[4]
        })

    selected_class_id = request.args.get("class_id")
    selected_class = None
    students_data = []

    if selected_class_id and str(selected_class_id).isdigit():
        selected_class_id = int(selected_class_id)
        cur.execute(
            "SELECT id, subject, course, semester, DATE_FORMAT(class_date, '%%d/%%m/%%Y') FROM class_sessions WHERE id=%s AND teacher_id=%s",
            (selected_class_id, teacher_id)
        )
        sc = cur.fetchone()
        if sc:
            selected_class = {
                'id': sc[0],
                'subject': sc[1],
                'course': sc[2],
                'semester': sc[3],
                'class_date_str': sc[4]
            }

            # Fetch students matching course & semester
            cur.execute(
                "SELECT id, name, roll_no, course, semester FROM students WHERE LOWER(course)=LOWER(%s) AND LOWER(semester)=LOWER(%s)",
                (sc[2], sc[3])
            )
            stu_rows = cur.fetchall()

            for s in stu_rows:
                stu_id, stu_name, stu_roll, stu_course, stu_sem = s

                # Check attendance for this class
                cur.execute("SELECT id FROM attendance WHERE class_id=%s AND student_id=%s", (selected_class_id, stu_id))
                attended = bool(cur.fetchone())

                # Calculate real engagement average
                cur.execute(
                    "SELECT AVG(engagement_score) FROM engagement WHERE student_id=%s AND class_id=%s",
                    (stu_id, selected_class_id)
                )
                eng_res = cur.fetchone()
                avg_eng = round(float(eng_res[0]), 1) if (eng_res and eng_res[0] is not None) else (85.0 if attended else 0.0)

                # Latest attention level
                cur.execute(
                    "SELECT attention_level FROM engagement WHERE student_id=%s AND class_id=%s ORDER BY id DESC LIMIT 1",
                    (stu_id, selected_class_id)
                )
                att_res = cur.fetchone()
                att_level = att_res[0] if att_res else ("Attentive" if attended else "Absent")

                # Phone & Distraction info check
                cur.execute(
                    "SELECT attention_level FROM engagement WHERE student_id=%s AND class_id=%s AND attention_level LIKE '%%PHONE%%'",
                    (stu_id, selected_class_id)
                )
                phone_row = cur.fetchone()
                phone_info = "Phone Detected" if phone_row else "No Phone Detected"

                # Check if report already generated
                cur.execute("SELECT id FROM reports WHERE student_id=%s AND class_id=%s", (stu_id, selected_class_id))
                has_report = bool(cur.fetchone())

                students_data.append({
                    'id': stu_id,
                    'name': stu_name,
                    'roll_no': stu_roll,
                    'attended': attended,
                    'avg_engagement': avg_eng,
                    'attention_level': att_level,
                    'phone_info': phone_info,
                    'has_report': has_report
                })

    # Fetch generated reports list for this teacher
    cur.execute(
        """
        SELECT r.id, s.name, s.roll_no, cs.subject, r.report_title, r.attendance_percentage, r.avg_engagement,
               r.overall_status, DATE_FORMAT(r.created_at, '%%d/%%m/%%Y %%h:%%i %%p')
        FROM reports r
        JOIN students s ON r.student_id = s.id
        JOIN class_sessions cs ON r.class_id = cs.id
        WHERE r.teacher_id=%s
        ORDER BY r.created_at DESC
        """,
        (teacher_id,)
    )
    rep_rows = cur.fetchall()
    cur.close()

    generated_reports = []
    for r in rep_rows:
        generated_reports.append({
            'id': r[0],
            'student_name': r[1],
            'roll_no': r[2],
            'subject': r[3],
            'report_title': r[4],
            'attendance_percentage': r[5],
            'avg_engagement': r[6],
            'overall_status': r[7],
            'created_at_str': r[8]
        })

    return render_template(
        "teacher/reports.html",
        classes=classes,
        selected_class_id=selected_class_id,
        selected_class=selected_class,
        students_data=students_data,
        generated_reports=generated_reports
    )


# -------------------------
# Teacher - Generate Report for Student
# -------------------------
@app.route("/teacher/generate-report", methods=["POST"])
def teacher_generate_report():

    if "teacher" not in session:
        return redirect(url_for("login"))

    email = session["teacher"]
    student_id = request.form.get("student_id")
    class_id = request.form.get("class_id")
    report_title = request.form.get("report_title", "").strip()
    summary = request.form.get("summary", "").strip()

    if not all([student_id, class_id, report_title, summary]):
        flash("All report fields are required.")
        return redirect(url_for("teacher_reports"))

    cur = mysql.connection.cursor()
    cur.execute("SELECT id FROM teachers WHERE email=%s", (email,))
    teacher = cur.fetchone()

    if not teacher:
        cur.close()
        flash("Teacher account not found.")
        return redirect(url_for("login"))

    teacher_id = teacher[0]

    # Calculate real engagement average
    cur.execute(
        "SELECT AVG(engagement_score) FROM engagement WHERE student_id=%s AND class_id=%s",
        (student_id, class_id)
    )
    eng_res = cur.fetchone()
    avg_eng = round(float(eng_res[0]), 1) if (eng_res and eng_res[0] is not None) else 85.0

    # Calculate latest attention level
    cur.execute(
        "SELECT attention_level FROM engagement WHERE student_id=%s AND class_id=%s ORDER BY id DESC LIMIT 1",
        (student_id, class_id)
    )
    att_res = cur.fetchone()
    attention_lvl = att_res[0] if att_res else "Attentive"

    # Calculate real attendance status
    cur.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id=%s AND class_id=%s",
        (student_id, class_id)
    )
    att_count = cur.fetchone()[0]
    att_pct = 100.0 if att_count > 0 else 0.0

    # Phone detection info
    cur.execute(
        "SELECT attention_level FROM engagement WHERE student_id=%s AND class_id=%s AND attention_level LIKE '%%PHONE%%'",
        (student_id, class_id)
    )
    phone_res = cur.fetchone()
    phone_info = "Phone Detected" if phone_res else "No Phone Detected"

    distraction_info = "Head Movement Normal" if str(attention_lvl).upper() in ("ATTENTIVE", "ATTENTIVE") else f"Distraction Noted ({attention_lvl})"
    overall_status = "Good Performance" if (avg_eng >= 70.0 and att_pct > 0) else "Needs Improvement"

    # Upsert (Insert or Update existing report to prevent duplicate entries for same student & class)
    cur.execute("SELECT id FROM reports WHERE student_id=%s AND class_id=%s", (student_id, class_id))
    existing_rep = cur.fetchone()

    if existing_rep:
        cur.execute(
            """
            UPDATE reports
            SET teacher_id=%s, report_title=%s, summary=%s, attendance_percentage=%s,
                avg_engagement=%s, attention_level=%s, phone_info=%s, distraction_info=%s,
                overall_status=%s, created_at=NOW()
            WHERE student_id=%s AND class_id=%s
            """,
            (teacher_id, report_title, summary, att_pct, avg_eng, attention_lvl, phone_info, distraction_info, overall_status, student_id, class_id)
        )
    else:
        cur.execute(
            """
            INSERT INTO reports (teacher_id, student_id, class_id, report_title, summary, attendance_percentage, avg_engagement, attention_level, phone_info, distraction_info, overall_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (teacher_id, student_id, class_id, report_title, summary, att_pct, avg_eng, attention_lvl, phone_info, distraction_info, overall_status)
        )

    mysql.connection.commit()
    cur.close()

    flash("Student report generated successfully!")
    return redirect(url_for("teacher_reports", class_id=class_id))


def get_admin_class_counts(cur):
    sync_class_session_statuses(cur)
    cur.execute("SELECT id, subject, course, semester, class_date, start_time, end_time, status FROM class_sessions")
    all_classes = cur.fetchall()
    now_ist = get_now_ist()

    total_classes = len(all_classes)
    active_count = 0
    completed_count = 0
    upcoming_count = 0

    completed_classes = []
    for c in all_classes:
        c_id, subj, course, semester, c_date, s_time, e_time, status = c
        st = classify_class_status(c_date, s_time, e_time, status, now_ist)
        if st == 'active':
            active_count += 1
        elif st == 'completed':
            completed_count += 1
            completed_classes.append({
                'id': c_id,
                'subject': subj,
                'course': course,
                'semester': semester
            })
        elif st == 'upcoming':
            upcoming_count += 1

    # 1. Student-Wise Subject Performance
    cur.execute("SELECT id, name, roll_no, course, semester FROM students ORDER BY name ASC")
    students = cur.fetchall()

    student_subject_performance = []
    for s in students:
        s_id, s_name, s_roll, s_course, s_sem = s
        comp_subjs = sorted(list(set([c['subject'] for c in completed_classes if c['course'].lower() == s_course.lower() and c['semester'].lower() == s_sem.lower()])))

        for subj in comp_subjs:
            comp_ids = [c['id'] for c in completed_classes if c['subject'].lower() == subj.lower() and c['course'].lower() == s_course.lower() and c['semester'].lower() == s_sem.lower()]
            comp_cnt = len(comp_ids)

            if comp_ids:
                format_strings = ','.join(['%s'] * len(comp_ids))
                cur.execute(
                    f"SELECT COUNT(*) FROM attendance WHERE student_id = %s AND class_id IN ({format_strings}) AND LOWER(status) = 'present'",
                    (s_id, *comp_ids)
                )
                att_cnt = cur.fetchone()[0]
                att_pct_str = f"{round((att_cnt / comp_cnt) * 100.0, 1)}%"

                cur.execute(
                    f"""
                    SELECT AVG(e.engagement_score)
                    FROM engagement e
                    JOIN attendance a ON e.student_id = a.student_id AND e.class_id = a.class_id
                    WHERE e.student_id = %s AND e.class_id IN ({format_strings}) AND LOWER(a.status) = 'present'
                    """,
                    (s_id, *comp_ids)
                )
                eng_res = cur.fetchone()
                eng_pct_str = f"{round(float(eng_res[0]), 1)}%" if (eng_res and eng_res[0] is not None) else "N/A"
            else:
                att_pct_str = "N/A"
                eng_pct_str = "N/A"

            student_subject_performance.append({
                'student_name': s_name,
                'roll_no': s_roll,
                'subject': subj,
                'completed_classes': comp_cnt,
                'attendance_str': att_pct_str,
                'engagement_str': eng_pct_str
            })

    # 2. Subject-Wise Summary
    cur.execute("SELECT DISTINCT subject FROM class_sessions ORDER BY subject ASC")
    all_subjects = [r[0] for r in cur.fetchall()]

    subject_summary = []
    for subj in all_subjects:
        subj_comp_classes = [c for c in completed_classes if c['subject'].lower() == subj.lower()]
        comp_cnt = len(subj_comp_classes)
        comp_ids = [c['id'] for c in subj_comp_classes]

        enrolled_students_set = set()
        for c in subj_comp_classes:
            cur.execute("SELECT id FROM students WHERE LOWER(course) = LOWER(%s) AND LOWER(semester) = LOWER(%s)", (c['course'], c['semester']))
            stus = cur.fetchall()
            for st_row in stus:
                enrolled_students_set.add(st_row[0])

        enrolled_cnt = len(enrolled_students_set)

        if comp_ids and enrolled_cnt > 0:
            expected_records = comp_cnt * enrolled_cnt
            format_strings = ','.join(['%s'] * len(comp_ids))

            cur.execute(
                f"SELECT COUNT(DISTINCT CONCAT(class_id, '_', student_id)) FROM attendance WHERE class_id IN ({format_strings}) AND LOWER(status) = 'present'",
                tuple(comp_ids)
            )
            present_cnt = cur.fetchone()[0]
            att_rate_str = f"{round((present_cnt / expected_records) * 100.0, 1)}%" if expected_records > 0 else "0.0%"

            cur.execute(
                f"""
                SELECT AVG(e.engagement_score)
                FROM engagement e
                JOIN attendance a ON e.student_id = a.student_id AND e.class_id = a.class_id
                WHERE e.class_id IN ({format_strings}) AND LOWER(a.status) = 'present'
                """,
                tuple(comp_ids)
            )
            eng_res = cur.fetchone()
            avg_eng_str = f"{round(float(eng_res[0]), 1)}%" if (eng_res and eng_res[0] is not None) else "N/A"
        else:
            att_rate_str = "0.0%"
            avg_eng_str = "N/A"

        subject_summary.append({
            'subject': subj,
            'enrolled_students': enrolled_cnt,
            'completed_classes': comp_cnt,
            'attendance_rate': att_rate_str,
            'avg_engagement': avg_eng_str
        })

    # Terminal log
    print("\n" + "=" * 50)
    print("ADMIN DASHBOARD PERFORMANCE TABLES LOADED")
    print("-" * 50)
    print(f"Total Classes: {total_classes} | Active: {active_count} | Completed: {completed_count}")
    print(f"Student-Wise Subject Entries: {len(student_subject_performance)}")
    print(f"Subject-Wise Summary Entries: {len(subject_summary)}")
    print("=" * 50 + "\n")

    return total_classes, active_count, completed_count, upcoming_count, student_subject_performance, subject_summary


# -------------------------
# Admin Module Routes
# -------------------------

@app.route("/admin/dashboard")
@app.route("/admin-dashboard")
def admin_dashboard():

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()

    cur.execute("SELECT * FROM students ORDER BY id DESC")
    students = cur.fetchall()

    cur.execute("SELECT COUNT(*) FROM students")
    total_students = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM teachers")
    total_teachers = cur.fetchone()[0]

    total_classes, active_classes, completed_classes, upcoming_classes, student_subject_performance, subject_summary = get_admin_class_counts(cur)

    cur.close()

    return render_template(
        "admin/dashboard.html",
        students=students,
        total_students=total_students,
        total_teachers=total_teachers,
        total_classes=total_classes,
        active_classes=active_classes,
        completed_classes=completed_classes,
        student_subject_performance=student_subject_performance,
        subject_summary=subject_summary
    )


@app.route("/admin/students")
def admin_students():

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, roll_no, email, phone, course, semester FROM students ORDER BY id DESC")
    students = cur.fetchall()
    cur.close()

    return render_template("admin/students.html", students=students)


@app.route("/admin/student/edit/<int:student_id>", methods=["POST"])
def admin_edit_student(student_id):

    if "admin" not in session:
        return redirect(url_for("login"))

    name = request.form.get("name", "").strip()
    roll_no = request.form.get("roll_no", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    course = request.form.get("course", "").strip()
    semester = request.form.get("semester", "").strip()

    if not all([name, roll_no, email, phone, course, semester]):
        flash("All student fields are required.")
        return redirect(url_for("admin_students"))

    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE students
        SET name=%s, roll_no=%s, email=%s, phone=%s, course=%s, semester=%s
        WHERE id=%s
    """, (name, roll_no, email, phone, course, semester, student_id))
    mysql.connection.commit()
    cur.close()

    flash("Student details updated successfully.")
    return redirect(url_for("admin_students"))


@app.route("/admin/student/delete/<int:student_id>", methods=["POST"])
def admin_delete_student(student_id):

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM students WHERE id=%s", (student_id,))
    mysql.connection.commit()
    cur.close()

    flash("Student deleted successfully.")
    return redirect(url_for("admin_students"))


@app.route("/admin/teachers")
def admin_teachers():

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()
    cur.execute("SELECT id, name, employee_id, department, email, phone FROM teachers ORDER BY id DESC")
    teachers = cur.fetchall()
    cur.close()

    return render_template("admin/teachers.html", teachers=teachers)


@app.route("/admin/teacher/edit/<int:teacher_id>", methods=["POST"])
def admin_edit_teacher(teacher_id):

    if "admin" not in session:
        return redirect(url_for("login"))

    name = request.form.get("name", "").strip()
    employee_id = request.form.get("employee_id", "").strip()
    department = request.form.get("department", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()

    if not all([name, employee_id, department, email, phone]):
        flash("All teacher fields are required.")
        return redirect(url_for("admin_teachers"))

    cur = mysql.connection.cursor()
    cur.execute("""
        UPDATE teachers
        SET name=%s, employee_id=%s, department=%s, email=%s, phone=%s
        WHERE id=%s
    """, (name, employee_id, department, email, phone, teacher_id))
    mysql.connection.commit()
    cur.close()

    flash("Teacher details updated successfully.")
    return redirect(url_for("admin_teachers"))


@app.route("/admin/teacher/delete/<int:teacher_id>", methods=["POST"])
def admin_delete_teacher(teacher_id):

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()
    cur.execute("DELETE FROM teachers WHERE id=%s", (teacher_id,))
    mysql.connection.commit()
    cur.close()

    flash("Teacher deleted successfully.")
    return redirect(url_for("admin_teachers"))


@app.route("/admin/analytics")
def admin_analytics():

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()

    cur.execute("SELECT COUNT(*) FROM students")
    total_students = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM teachers")
    total_teachers = cur.fetchone()[0]

    total_classes, active_classes, completed_classes, upcoming_classes, student_subject_performance, subject_summary = get_admin_class_counts(cur)

    cur.close()

    return render_template(
        "admin/analytics.html",
        total_students=total_students,
        total_teachers=total_teachers,
        total_classes=total_classes,
        active_classes=active_classes,
        completed_classes=completed_classes,
        student_subject_performance=student_subject_performance,
        subject_summary=subject_summary
    )


@app.route("/admin/reports")
def admin_reports():

    if "admin" not in session:
        return redirect(url_for("login"))

    cur = mysql.connection.cursor()
    cur.execute("""
        SELECT r.id, 
               COALESCE(s.name, 'N/A') AS student_name, 
               COALESCE(s.roll_no, 'N/A') AS roll_no, 
               COALESCE(cs.subject, r.report_title) AS subject, 
               r.attendance_percentage, 
               r.avg_engagement, 
               r.attention_level, 
               r.overall_status, 
               r.summary,
               r.created_at,
               COALESCE(t.name, 'N/A') AS teacher_name
        FROM reports r
        LEFT JOIN students s ON r.student_id = s.id
        LEFT JOIN teachers t ON r.teacher_id = t.id
        LEFT JOIN class_sessions cs ON r.class_id = cs.id
        ORDER BY r.created_at DESC
    """)
    raw_reports = cur.fetchall()
    cur.close()

    reports = []
    for rep in raw_reports:
        r_id, s_name, roll_no, subject, att_pct, eng_score, att_lvl, status, summary, created_at, teacher_name = rep
        if isinstance(created_at, datetime):
            report_date_str = created_at.strftime('%d/%m/%Y %I:%M %p')
        elif created_at:
            report_date_str = str(created_at)
        else:
            report_date_str = "N/A"

        reports.append({
            'id': r_id,
            'student_name': s_name,
            'roll_no': roll_no,
            'subject': subject,
            'attendance_percentage': round(float(att_pct), 1) if att_pct is not None else 0.0,
            'avg_engagement': round(float(eng_score), 1) if eng_score is not None else 0.0,
            'attention_level': att_lvl or "N/A",
            'overall_status': status or "N/A",
            'summary': summary or "N/A",
            'created_at_str': report_date_str,
            'teacher_name': teacher_name
        })

    return render_template("admin/reports.html", reports=reports)


@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():

    if "admin" not in session:
        return redirect(url_for("login"))

    email = session["admin"]
    cur = mysql.connection.cursor()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        new_password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if new_password:
            if len(new_password) < 6:
                flash("Password must be at least 6 characters long.")
                cur.close()
                return redirect(url_for("admin_settings"))
            if new_password != confirm_password:
                flash("Passwords do not match.")
                cur.close()
                return redirect(url_for("admin_settings"))
            
            cur.execute("UPDATE admins SET name=%s, password=%s WHERE email=%s", (name, new_password, email))
        else:
            cur.execute("UPDATE admins SET name=%s WHERE email=%s", (name, email))

        mysql.connection.commit()
        flash("Admin settings updated successfully!")
        cur.close()
        return redirect(url_for("admin_settings"))

    cur.execute("SELECT id, name, email FROM admins WHERE email=%s", (email,))
    admin_user = cur.fetchone()
    cur.close()

    return render_template("admin/settings.html", admin_user=admin_user)


# -------------------------
# Phone Detection
# -------------------------
@app.route("/detect-phone", methods=["POST"])
def detect_phone_route():

    try:

        # Get image from browser
        image_file = request.files.get("image") or request.files.get("frame")

        if not image_file:

            return jsonify({
                "phone_detected": False,
                "message": "No image received"
            })

        # Read image
        image_bytes = image_file.read()

        # Convert image to NumPy array
        image_array = np.frombuffer(
            image_bytes,
            np.uint8
        )

        # Convert to OpenCV image
        frame = cv2.imdecode(
            image_array,
            cv2.IMREAD_COLOR
        )

        if frame is None:

            return jsonify({
                "phone_detected": False,
                "message": "Unable to read image"
            })

        # -------------------------
        # AI ENGAGEMENT & PHONE DETECTION
        # -------------------------
        processed_frame, phone_detected, engagement_status, result_dict = detect_phone(frame)

        # Encode processed frame with bounding box to base64 JPEG
        _, buffer = cv2.imencode(".jpg", processed_frame)
        frame_base64 = base64.b64encode(buffer).decode("utf-8")

        # Save engagement data to database linked to student & active class
        email = session.get("student")
        req_class_id = request.form.get("class_id") or session.get("joined_class_id") or session.get("active_class_id")

        score = float(result_dict.get("engagement_score", 100.0))
        att_level = result_dict.get("attention_level", engagement_status)
        face_status = result_dict.get("face_status", "FACE DETECTED")
        eye_status = result_dict.get("eye_status", "EYES ON SCREEN")
        drowsiness_status = result_dict.get("drowsiness_status", "ALERT")
        phone_status = result_dict.get("phone_status", "NO PHONE DETECTED")
        engagement_level = result_dict.get("engagement_level", "HIGH")

        if email:
            try:
                cur = mysql.connection.cursor()
                cur.execute("SELECT id FROM students WHERE email=%s", (email,))
                stu = cur.fetchone()
                if stu:
                    stu_id = stu[0]

                    if req_class_id and str(req_class_id).isdigit():
                        cur.execute("SELECT status FROM class_sessions WHERE id=%s", (int(req_class_id),))
                        cls_st = cur.fetchone()
                        if cls_st and cls_st[0].lower() in ['completed', 'not taken']:
                            cur.close()
                            return jsonify({
                                "camera_stop": True,
                                "message": "Class session has ended. Camera monitoring stopped.",
                                "phone_detected": False,
                                "face_status": "CLASS ENDED",
                                "attention_status": "CLASS ENDED",
                                "eye_status": "CLASS ENDED",
                                "drowsiness_status": "CLASS ENDED",
                                "phone_status": "CLASS ENDED",
                                "engagement_score": score,
                                "engagement_level": "LOW",
                                "engagement_status": "CLASS ENDED"
                            })

                        cur.execute(
                            """
                            INSERT INTO engagement (
                                student_id, class_id, engagement_score, attendance_status,
                                attention_level, face_status, eye_status, drowsiness_status, phone_status, recorded_at
                            )
                            VALUES (%s, %s, %s, 'Present', %s, %s, %s, %s, %s, NOW())
                            """,
                            (stu_id, int(req_class_id), score, att_level, face_status, eye_status, drowsiness_status, phone_status)
                        )
                    else:
                        cur.execute(
                            """
                            INSERT INTO engagement (
                                student_id, engagement_score, attendance_status,
                                attention_level, face_status, eye_status, drowsiness_status, phone_status, recorded_at
                            )
                            VALUES (%s, %s, 'Present', %s, %s, %s, %s, %s, NOW())
                            """,
                            (stu_id, score, att_level, face_status, eye_status, drowsiness_status, phone_status)
                        )
                    mysql.connection.commit()
                cur.close()
            except Exception as db_err:
                print("Engagement recording DB error:", db_err)

        # Return comprehensive multi-detector results to browser
        return jsonify({
            "face_status": face_status,
            "attention_status": result_dict.get("attention_status", "LOOKING STRAIGHT"),
            "eye_status": eye_status,
            "drowsiness_status": drowsiness_status,
            "phone_status": phone_status,
            "phone_detected": bool(phone_detected),
            "phone_message": (
                "📱 PHONE DETECTED"
                if phone_detected
                else "✅ NO PHONE DETECTED"
            ),
            "engagement_score": score,
            "engagement_level": engagement_level,
            "engagement_status": engagement_status,
            "image": f"data:image/jpeg;base64,{frame_base64}"
        })

    except Exception as e:

        print("Phone detection error:", e)

        return jsonify({
            "phone_detected": False,
            "message": str(e)
        })

# -------------------------
# Logout
# -------------------------
@app.route("/logout")
def logout():

    session.clear()

    return redirect(url_for("login"))


# -------------------------
# About
# -------------------------
@app.route("/about")
def about():

    return render_template("about.html")


# -------------------------
# Features
# -------------------------
@app.route("/features")
def features():

    return render_template("features.html")


# -------------------------
# Contact
# -------------------------
@app.route("/contact")
def contact():

    return render_template("contact.html")


# -------------------------
# Run Flask
# -------------------------
if __name__ == "__main__":
    app.run(debug=True)