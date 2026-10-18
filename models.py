from datetime import datetime
from database import db


class Student(db.Model):
    __tablename__ = 'students'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    roll_no = db.Column(db.String(50), nullable=False, unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    phone = db.Column(db.String(20), nullable=False)
    course = db.Column(db.String(100), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Foreign Key Relationships
    attendances = db.relationship('Attendance', backref='student', lazy=True, cascade="all, delete-orphan")
    engagements = db.relationship('Engagement', backref='student', lazy=True, cascade="all, delete-orphan")
    reports = db.relationship('Report', backref='student', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Student {self.roll_no} - {self.name}>"


class Teacher(db.Model):
    __tablename__ = 'teachers'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=False)
    employee_id = db.Column(db.String(50), nullable=False, unique=True)
    department = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), nullable=False, unique=True)
    phone = db.Column(db.String(20), nullable=False)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Foreign Key Relationships
    class_sessions = db.relationship('ClassSession', backref='teacher', lazy=True, cascade="all, delete-orphan")
    reports = db.relationship('Report', backref='teacher', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Teacher {self.employee_id} - {self.name}>"


class Admin(db.Model):
    __tablename__ = 'admins'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Admin {self.email}>"


class ClassSession(db.Model):
    __tablename__ = 'class_sessions'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id', ondelete='CASCADE'), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    course = db.Column(db.String(100), nullable=False)
    semester = db.Column(db.String(20), nullable=False)
    class_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='Scheduled')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Foreign Key Relationships
    attendances = db.relationship('Attendance', backref='class_session', lazy=True, cascade="all, delete-orphan")
    engagements = db.relationship('Engagement', backref='class_session', lazy=True)
    reports = db.relationship('Report', backref='class_session', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ClassSession {self.subject} ({self.course} Sem {self.semester}) - {self.class_date}>"


class Attendance(db.Model):
    __tablename__ = 'attendance'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    class_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(20), nullable=False, default='PRESENT')
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('class_id', 'student_id', name='unique_class_student'),
    )

    def __repr__(self):
        return f"<Attendance Student:{self.student_id} Class:{self.class_id} Status:{self.status}>"


class Engagement(db.Model):
    __tablename__ = 'engagement'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id', ondelete='SET NULL'), nullable=True)
    engagement_score = db.Column(db.Float, nullable=False)
    attendance_status = db.Column(db.String(20), nullable=False, default='Present')
    attention_level = db.Column(db.String(50), nullable=True)
    face_status = db.Column(db.String(50), nullable=True)
    eye_status = db.Column(db.String(50), nullable=True)
    drowsiness_status = db.Column(db.String(50), nullable=True)
    phone_status = db.Column(db.String(50), nullable=True)
    recorded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Engagement Student:{self.student_id} Class:{self.class_id} Score:{self.engagement_score}>"


class Report(db.Model):
    __tablename__ = 'reports'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id', ondelete='CASCADE'), nullable=False)
    report_title = db.Column(db.String(255), nullable=False)
    summary = db.Column(db.Text, nullable=False)
    attendance_percentage = db.Column(db.Float, default=0.0)
    avg_engagement = db.Column(db.Float, default=0.0)
    attention_level = db.Column(db.String(50), nullable=True)
    phone_info = db.Column(db.String(100), nullable=True)
    distraction_info = db.Column(db.String(100), nullable=True)
    overall_status = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('student_id', 'class_id', name='unique_student_class_report'),
    )

    def __repr__(self):
        return f"<Report {self.report_title} - Student:{self.student_id}>"
