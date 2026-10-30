"""
God Dominion School — Website + School Portal
Flask + SQLAlchemy on Supabase (Postgres). SQLite is used only when DATABASE_URL is empty (local testing).

Roles: admin, teacher, student, parent
Payments: Flutterwave. Email: any SMTP server (Gmail, Zoho, Brevo, Resend SMTP...).
"""
import os, csv, io, hmac, secrets, string, re, uuid, threading, smtplib, logging
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, abort, Response, g)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, UniqueConstraint
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # correct https links behind Render/Railway
if os.getenv("RENDER") or os.getenv("SITE_URL", "").startswith("https://"):
    app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      PREFERRED_URL_SCHEME="https")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")
log = logging.getLogger("school")
logging.basicConfig(level=logging.INFO)

db_url = os.getenv("DATABASE_URL", "").strip()
USING_SUPABASE = bool(db_url)
if not db_url:
    db_url = "sqlite:///school.db"
    log.warning("DATABASE_URL is empty: using local SQLite for testing. Set it to your Supabase connection string to go live.")
else:
    # Supabase gives postgres:// or postgresql:// — use the psycopg 3 driver (works on Python 3.10 to 3.14)
    db_url = re.sub(r"^postgres(ql)?(\+\w+)?://", "postgresql+psycopg://", db_url)
    if "sslmode=" not in db_url:
        db_url += ("&" if "?" in db_url else "?") + "sslmode=require"
    if os.getenv("DB_SSL_DISABLE") == "1":
        db_url = db_url.replace("sslmode=require", "sslmode=disable")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
engine_opts = {"pool_pre_ping": True}
if USING_SUPABASE:
    # prepare_threshold=None keeps it compatible with Supabase's transaction pooler (port 6543)
    engine_opts.update(pool_size=5, max_overflow=5, pool_recycle=280, connect_args={"prepare_threshold": None})
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = engine_opts
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
db = SQLAlchemy(app)

FLW_SECRET = os.getenv("FLW_SECRET_KEY", "").strip()
FLW_HASH = os.getenv("FLW_SECRET_HASH", "").strip()
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")
SMTP = {"host": os.getenv("SMTP_HOST", "").strip(), "port": int(os.getenv("SMTP_PORT", "587") or 587),
        "user": os.getenv("SMTP_USER", "").strip(), "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "sender": os.getenv("MAIL_FROM", "").strip(), "ssl": os.getenv("SMTP_SSL", "") == "1"}
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "school-media")
LEVELS = ["JSS1", "JSS2", "JSS3", "SS1", "SS2", "SS3"]
TERMS = ["First Term", "Second Term", "Third Term"]


# ─────────────────────────── MODELS ───────────────────────────
class Setting(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, default="")


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False)  # admin / teacher / student
    full_name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128))
    phone = db.Column(db.String(32))
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class AcademicSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(16), unique=True, nullable=False)  # 2026/2027


class SchoolClass(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(32), unique=True, nullable=False)  # JSS 1A
    level = db.Column(db.String(16), nullable=False)               # JSS1..SS3
    form_teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    form_teacher = db.relationship("User")


class Subject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    code = db.Column(db.String(16))


class ClassSubject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    school_class = db.relationship("SchoolClass")
    subject = db.relationship("Subject")
    teacher = db.relationship("User")
    __table_args__ = (UniqueConstraint("class_id", "subject_id"),)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    reg_no = db.Column(db.String(32), unique=True, nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"))
    gender = db.Column(db.String(8))
    dob = db.Column(db.String(16))
    guardian_name = db.Column(db.String(128))
    guardian_phone = db.Column(db.String(32))
    address = db.Column(db.String(256))
    status = db.Column(db.String(16), default="active")  # active / graduated / withdrawn
    guardian_email = db.Column(db.String(128))
    photo_url = db.Column(db.String(500))
    blood_group = db.Column(db.String(8))
    medical_note = db.Column(db.String(300))
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    user = db.relationship("User", foreign_keys=[user_id])
    parent = db.relationship("User", foreign_keys=[parent_id])
    school_class = db.relationship("SchoolClass")


class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"), nullable=False)
    term = db.Column(db.String(16), nullable=False)
    ca1 = db.Column(db.Float, default=0)
    ca2 = db.Column(db.Float, default=0)
    exam = db.Column(db.Float, default=0)
    updated_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    subject = db.relationship("Subject")
    __table_args__ = (UniqueConstraint("student_id", "subject_id", "session_id", "term"),)

    @property
    def total(self):
        return round((self.ca1 or 0) + (self.ca2 or 0) + (self.exam or 0), 1)


class Remark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"), nullable=False)
    term = db.Column(db.String(16), nullable=False)
    teacher_comment = db.Column(db.Text, default="")
    principal_comment = db.Column(db.Text, default="")
    days_present = db.Column(db.Integer, default=0)
    days_open = db.Column(db.Integer, default=0)
    punctuality = db.Column(db.String(2), default="")
    neatness = db.Column(db.String(2), default="")
    conduct = db.Column(db.String(2), default="")
    __table_args__ = (UniqueConstraint("student_id", "session_id", "term"),)


class Publication(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"), nullable=False)
    term = db.Column(db.String(16), nullable=False)
    published_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("class_id", "session_id", "term"),)


class ScratchCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    serial = db.Column(db.String(16), unique=True, nullable=False)
    pin = db.Column(db.String(16), unique=True, nullable=False)
    max_uses = db.Column(db.Integer, default=5)
    used_count = db.Column(db.Integer, default=0)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))  # bound on first use
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"))
    term = db.Column(db.String(16))
    batch = db.Column(db.String(32))
    source = db.Column(db.String(16), default="admin")  # admin / online
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    student = db.relationship("Student")


class FeeItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"), nullable=False)
    term = db.Column(db.String(16), nullable=False)
    level = db.Column(db.String(16), default="ALL")  # ALL or JSS1..SS3
    name = db.Column(db.String(64), nullable=False)
    amount = db.Column(db.Float, nullable=False)


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))  # empty for admission form fees
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"))
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"))
    term = db.Column(db.String(16))
    purpose = db.Column(db.String(16), default="fees")  # fees / pin / form
    amount = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(64), unique=True, nullable=False)
    status = db.Column(db.String(16), default="pending")  # pending/success/failed
    channel = db.Column(db.String(16), default="flutterwave")  # flutterwave/cash/bank/pos/demo
    payer_email = db.Column(db.String(128))
    paid_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    gateway_id = db.Column(db.String(64))
    note = db.Column(db.String(256))
    card_id = db.Column(db.Integer, db.ForeignKey("scratch_card.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)
    student = db.relationship("Student")
    application = db.relationship("Application", foreign_keys=[application_id])
    card = db.relationship("ScratchCard")


class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    body = db.Column(db.Text, nullable=False)
    audience = db.Column(db.String(16), default="all")  # all / teachers / students
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── Website content ──
class Post(db.Model):  # news article or event
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(8), default="news")  # news / event
    title = db.Column(db.String(160), nullable=False)
    slug = db.Column(db.String(180), unique=True, nullable=False)
    summary = db.Column(db.String(300), default="")
    body = db.Column(db.Text, default="")
    image_url = db.Column(db.String(500), default="")
    event_date = db.Column(db.Date)
    event_time = db.Column(db.String(32), default="")
    venue = db.Column(db.String(128), default="")
    published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GalleryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    image_url = db.Column(db.String(500), nullable=False)
    caption = db.Column(db.String(160), default="")
    category = db.Column(db.String(32), default="School life")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Testimonial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    role = db.Column(db.String(80), default="Parent")
    text = db.Column(db.Text, nullable=False)
    show = db.Column(db.Boolean, default=True)


class Application(db.Model):  # online admission
    id = db.Column(db.Integer, primary_key=True)
    app_no = db.Column(db.String(32), unique=True, nullable=False)
    full_name = db.Column(db.String(128), nullable=False)
    dob = db.Column(db.String(16))
    gender = db.Column(db.String(8))
    class_sought = db.Column(db.String(16))
    previous_school = db.Column(db.String(160))
    parent_name = db.Column(db.String(128), nullable=False)
    relationship = db.Column(db.String(32))
    phone = db.Column(db.String(32), nullable=False)
    email = db.Column(db.String(128))
    address = db.Column(db.String(256))
    heard_from = db.Column(db.String(64))
    notes = db.Column(db.Text)
    photo_url = db.Column(db.String(500))
    status = db.Column(db.String(16), default="submitted")  # submitted/reviewing/invited/admitted/declined
    exam_date = db.Column(db.String(64))
    admin_note = db.Column(db.Text)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"))
    token = db.Column(db.String(40), unique=True, default=lambda: secrets.token_urlsafe(16))
    form_paid = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ContactMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128))
    phone = db.Column(db.String(32))
    subject = db.Column(db.String(64))
    message = db.Column(db.Text, nullable=False)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── Portal extras ──
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    session_id = db.Column(db.Integer, db.ForeignKey("academic_session.id"), nullable=False)
    term = db.Column(db.String(16), nullable=False)
    day = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(1), nullable=False)  # P present / A absent / L late
    marked_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    __table_args__ = (UniqueConstraint("student_id", "day"),)


class Homework(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_subject_id = db.Column(db.Integer, db.ForeignKey("class_subject.id"), nullable=False)
    title = db.Column(db.String(160), nullable=False)
    details = db.Column(db.Text, default="")
    file_url = db.Column(db.String(500), default="")
    due_date = db.Column(db.Date)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    cs = db.relationship("ClassSubject")


class TimetableSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("school_class.id"), nullable=False)
    day = db.Column(db.Integer, nullable=False)       # 0 = Monday … 4 = Friday
    period = db.Column(db.Integer, nullable=False)    # index into the period list in settings
    subject_id = db.Column(db.Integer, db.ForeignKey("subject.id"))
    label = db.Column(db.String(32), default="")      # e.g. Break, Devotion, Clubs
    subject = db.relationship("Subject")
    __table_args__ = (UniqueConstraint("class_id", "day", "period"),)


class EmailLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False)
    kind = db.Column(db.String(32), default="general")
    status = db.Column(db.String(16), default="queued")  # queued / sent / failed / not_configured
    error = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────── HELPERS ───────────────────────────
DEFAULT_SETTINGS = {
    "school_name": "God Dominion School",
    "school_short": "GDS",
    "school_motto": "Truth and Province",
    "school_address": "Ogun State, Nigeria",
    "school_phone": "+234 800 000 0000",
    "school_phone2": "",
    "school_email": "info@goddominionschool.com",
    "logo_url": "",
    "principal_name": "The Principal",
    # website
    "hero_title": "Raising young people who lead with knowledge and faith",
    "hero_text": "A Christian secondary school where every student is known by name, taught by qualified teachers and prepared for WAEC, NECO and life beyond the classroom.",
    "founded_year": "2008",
    "stat_students": "650",
    "stat_teachers": "48",
    "stat_pass_rate": "96",
    "principal_welcome": "Welcome to God Dominion School. We believe every child carries a gift from God, and our job is to find it, train it and send it into the world with good character. Our teachers know each student personally, our classrooms are small enough for questions, and our standards are high enough to prepare students for any university they choose. Come and visit us; we would love to show you around.",
    "about_text": "God Dominion School was founded to give families in our community a secondary school that takes both academics and character seriously. From a few classrooms we have grown into a full junior and senior secondary school with science, ICT and arts facilities, a library and sports grounds, while keeping the close, family feel that parents value.",
    "mission": "To provide sound, Christ-centred education that develops each student's mind, character and gifts.",
    "vision": "To raise a generation of excellent, disciplined and God-fearing leaders for Nigeria and the world.",
    "admissions_open": "1",
    "admission_session_text": "Admission is open into JSS 1 to SS 2 for the coming session.",
    "office_hours": "Monday to Friday, 7:30am to 4:00pm",
    "whatsapp": "2348000000000",
    "facebook": "", "instagram": "", "youtube": "", "x_twitter": "",
    "map_query": "God Dominion School, Ogun State, Nigeria",
    "current_session_id": "",
    "current_term": "First Term",
    "next_term_begins": "",
    "pin_price": "1000",
    "pin_max_uses": "5",
    "block_result_if_owing": "0",
    "ca1_max": "20", "ca2_max": "20", "exam_max": "60",
    # admissions
    "form_fee": "0",
    # email notifications (1 = on)
    "email_on_application": "1", "email_on_status": "1", "email_on_payment": "1",
    "email_on_results": "1", "email_on_absence": "1", "email_on_homework": "0",
    "admin_notify_email": "",
    # timetable
    "periods": "08:00-08:40,08:40-09:20,09:20-10:00,10:00-10:30,10:30-11:10,11:10-11:50,11:50-12:30,12:30-13:10,13:10-13:50",
}

GRADES = [  # (min, grade, remark)
    (75, "A1", "Excellent"), (70, "B2", "Very Good"), (65, "B3", "Good"),
    (60, "C4", "Credit"), (55, "C5", "Credit"), (50, "C6", "Credit"),
    (45, "D7", "Pass"), (40, "E8", "Pass"), (0, "F9", "Fail"),
]


def grade_for(total):
    for mn, gr, rm in GRADES:
        if total >= mn:
            return gr, rm
    return "F9", "Fail"


def get_setting(key):
    s = db.session.get(Setting, key)
    return s.value if s else DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    s = db.session.get(Setting, key)
    if not s:
        s = Setting(key=key)
        db.session.add(s)
    s.value = str(value)


def current_session():
    sid = get_setting("current_session_id")
    s = db.session.get(AcademicSession, int(sid)) if sid else None
    return s or AcademicSession.query.order_by(AcademicSession.id.desc()).first()


def current_term():
    return get_setting("current_term") or "First Term"


def ordinal(n):
    if not n:
        return "-"
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def money(v):
    return "₦{:,.2f}".format(v or 0)


app.jinja_env.filters["money"] = money
app.jinja_env.filters["ordinal"] = ordinal


def rand_code(n, chars=string.digits):
    return "".join(secrets.choice(chars) for _ in range(n))


def new_card(session_id, term, batch, source="admin"):
    while True:
        pin = rand_code(12)
        serial = "SN" + rand_code(8)
        if not ScratchCard.query.filter((ScratchCard.pin == pin) | (ScratchCard.serial == serial)).first():
            break
    c = ScratchCard(pin=pin, serial=serial, session_id=session_id, term=term, batch=batch,
                    max_uses=int(get_setting("pin_max_uses") or 5), source=source)
    db.session.add(c)
    return c


def fee_summary(student, sess, term):
    level = student.school_class.level if student.school_class else ""
    items = FeeItem.query.filter_by(session_id=sess.id, term=term).filter(
        FeeItem.level.in_(["ALL", level])).all() if sess else []
    total = sum(i.amount for i in items)
    paid = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter_by(
        student_id=student.id, session_id=sess.id if sess else None, term=term,
        status="success", purpose="fees").scalar() or 0
    return items, total, paid, max(total - paid, 0)


def is_published(class_id, session_id, term):
    return Publication.query.filter_by(class_id=class_id, session_id=session_id, term=term).first() is not None


def class_results(class_id, session_id, term):
    """Return {student_id: {...}} with totals, averages and positions for a class."""
    students = Student.query.filter_by(class_id=class_id, status="active").all()
    scores = Score.query.filter_by(class_id=class_id, session_id=session_id, term=term).all()
    by_student, by_subject = {}, {}
    for s in scores:
        by_student.setdefault(s.student_id, []).append(s)
        by_subject.setdefault(s.subject_id, []).append(s.total)
    data = {}
    for st in students:
        sc = by_student.get(st.id, [])
        total = sum(x.total for x in sc)
        avg = round(total / len(sc), 2) if sc else 0
        data[st.id] = {"student": st, "scores": sc, "total": round(total, 1), "average": avg, "count": len(sc)}
    ranked = sorted([d for d in data.values() if d["count"]], key=lambda d: d["average"], reverse=True)
    pos, prev = 0, None
    for i, d in enumerate(ranked, 1):
        if d["average"] != prev:
            pos, prev = i, d["average"]
        d["position"] = pos
    # subject position + stats
    subj_stats = {sid: {"high": max(v), "low": min(v), "avg": round(sum(v) / len(v), 1)} for sid, v in by_subject.items()}
    subj_rank = {}
    for sid in by_subject:
        lst = sorted([s for s in scores if s.subject_id == sid], key=lambda x: x.total, reverse=True)
        p, pv = 0, None
        for i, s in enumerate(lst, 1):
            if s.total != pv:
                p, pv = i, s.total
            subj_rank[(s.student_id, sid)] = p
    return data, subj_stats, subj_rank, len(ranked)


def build_report(student, sess, term):
    data, subj_stats, subj_rank, n_in_class = class_results(student.class_id, sess.id, term)
    me = data.get(student.id, {"scores": [], "total": 0, "average": 0, "count": 0})
    rows = []
    for s in sorted(me["scores"], key=lambda x: x.subject.name):
        gr, rm = grade_for(s.total)
        st = subj_stats.get(s.subject_id, {})
        rows.append({"subject": s.subject.name, "ca1": s.ca1, "ca2": s.ca2, "exam": s.exam,
                     "total": s.total, "grade": gr, "remark": rm,
                     "position": ordinal(subj_rank.get((student.id, s.subject_id))),
                     "high": st.get("high"), "low": st.get("low"), "avg": st.get("avg")})
    remark = Remark.query.filter_by(student_id=student.id, session_id=sess.id, term=term).first()
    gr, _ = grade_for(me["average"])
    # Third term: show 1st, 2nd and 3rd term totals and the cumulative (annual) average per subject
    cumulative = None
    if term == "Third Term":
        prev = {t: {x.subject_id: x.total for x in Score.query.filter_by(student_id=student.id, session_id=sess.id, term=t)}
                for t in TERMS[:2]}
        sid_by_name = {x.subject.name: x.subject_id for x in me["scores"]}
        for r in rows:
            sid = sid_by_name.get(r["subject"])
            t1, t2 = prev["First Term"].get(sid), prev["Second Term"].get(sid)
            got = [v for v in (t1, t2, r["total"]) if v is not None]
            r["t1"], r["t2"], r["cum"] = t1, t2, round(sum(got) / len(got), 1)
            r["cum_grade"] = grade_for(r["cum"])[0]
        cums = [r["cum"] for r in rows]
        cumulative = round(sum(cums) / len(cums), 2) if cums else 0
    # attendance register fills in days present/open when the form teacher hasn't typed them
    att = attendance_summary(student, sess.id, term)
    return {"student": student, "session": sess, "term": term, "rows": rows,
            "total": me["total"], "average": me["average"], "overall_grade": gr,
            "position": me.get("position"), "n_in_class": n_in_class, "remark": remark,
            "cumulative": cumulative, "att": att,
            "next_term_begins": get_setting("next_term_begins"), "generated": datetime.now()}


def attendance_summary(student, session_id, term):
    recs = Attendance.query.filter_by(student_id=student.id, session_id=session_id, term=term).all()
    if not recs:
        return None
    days_open = db.session.query(func.count(func.distinct(Attendance.day))).filter_by(
        class_id=student.class_id, session_id=session_id, term=term).scalar() or len(recs)
    present = sum(1 for r in recs if r.status in ("P", "L"))
    return {"open": days_open, "present": present, "absent": sum(1 for r in recs if r.status == "A"),
            "late": sum(1 for r in recs if r.status == "L"), "rate": round(present * 100 / days_open) if days_open else 0}


# ─────────────────────────── EMAIL ───────────────────────────
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def valid_email(e):
    return bool(e and EMAIL_RE.match(e.strip()))


def abs_url(endpoint, **kw):
    """Absolute link for emails. Uses SITE_URL when set (needed in production)."""
    try:
        if SITE_URL:
            return SITE_URL + url_for(endpoint, **kw)
        return url_for(endpoint, _external=True, **kw)
    except RuntimeError:
        return (SITE_URL or "") + "/"


BREVO_KEY = os.getenv("BREVO_API_KEY", "").strip()


def mail_ready():
    if BREVO_KEY:
        return bool(SMTP["sender"])
    return bool(SMTP["host"] and (SMTP["sender"] or SMTP["user"]))


def _send_brevo(to, subject, html, text):
    """Send through Brevo's HTTPS API — works on hosts that block SMTP (e.g. Render free plan)."""
    reply = get_setting("school_email")
    body = {"sender": {"name": get_setting("school_name"), "email": SMTP["sender"]},
            "to": [{"email": to}], "subject": subject, "htmlContent": html, "textContent": text}
    if valid_email(reply):
        body["replyTo"] = {"email": reply}
    r = requests.post("https://api.brevo.com/v3/smtp/email", json=body, timeout=30,
                      headers={"api-key": BREVO_KEY, "accept": "application/json", "content-type": "application/json"})
    if r.status_code >= 300:
        raise RuntimeError(f"Brevo {r.status_code}: {r.text[:300]}")


def _deliver(log_id, to, subject, html, text):
    with app.app_context():
        entry = db.session.get(EmailLog, log_id)
        try:
            if BREVO_KEY:
                _send_brevo(to, subject, html, text)
                entry.status = "sent"
                db.session.commit()
                return
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = f'{get_setting("school_name")} <{SMTP["sender"] or SMTP["user"]}>'
            msg["To"] = to
            reply_to = get_setting("school_email")
            if valid_email(reply_to):
                msg["Reply-To"] = reply_to
            msg.set_content(text)
            msg.add_alternative(html, subtype="html")
            if SMTP["ssl"] or SMTP["port"] == 465:
                server = smtplib.SMTP_SSL(SMTP["host"], SMTP["port"], timeout=30)
            else:
                server = smtplib.SMTP(SMTP["host"], SMTP["port"], timeout=30)
                server.starttls()
            if SMTP["user"]:
                server.login(SMTP["user"], SMTP["password"])
            server.send_message(msg)
            server.quit()
            entry.status = "sent"
        except Exception as e:  # keep the app running even if email fails
            entry.status, entry.error = "failed", str(e)[:480]
            log.warning("Email to %s failed: %s", to, e)
        db.session.commit()


def send_email(to, subject, template, kind="general", **ctx):
    """Render templates/email/<template>.html and send it in the background. Every email is logged."""
    if not valid_email(to):
        return None
    ctx.setdefault("S", {k: get_setting(k) for k in DEFAULT_SETTINGS})
    ctx["badge_url"] = abs_url("static", filename="img/crest.png")
    ctx["home_url"] = abs_url("home")
    html = render_template(f"email/{template}.html", **ctx)
    text = re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>|</p>|</tr>|</h\d>", "\n", html)))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    entry = EmailLog(to=to.strip(), subject=subject[:200], body=html, kind=kind,
                     status="queued" if mail_ready() else "not_configured")
    db.session.add(entry)
    db.session.commit()
    if mail_ready():
        if app.config.get("TESTING"):
            entry.status = "sent"; db.session.commit()
        else:
            threading.Thread(target=_deliver, args=(entry.id, to.strip(), subject, html, text), daemon=True).start()
    return entry


def setting_on(key):
    return get_setting(key) == "1"


def guardian_email(student):
    if student.guardian_email and valid_email(student.guardian_email):
        return student.guardian_email
    if student.parent and valid_email(student.parent.email or ""):
        return student.parent.email
    return None


# ─────────────────────────── AUTH / CSRF ───────────────────────────
@app.before_request
def load_user():
    g.user = db.session.get(User, session["uid"]) if session.get("uid") else None
    if g.user and not g.user.active:
        session.clear(); g.user = None
    if request.method == "POST" and request.endpoint not in ("flutterwave_webhook",):
        token = request.form.get("csrf_token")
        if not token or token != session.get("csrf"):
            abort(400, "Invalid form token — reload the page and try again.")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject():
    sess = current_session()
    return {"csrf_token": csrf_token, "S": {k: get_setting(k) for k in DEFAULT_SETTINGS},
            "cur_session": sess, "cur_term": current_term(), "TERMS": TERMS, "user": g.get("user"),
            "pay_live": bool(FLW_SECRET), "mail_live": mail_ready(), "now": datetime.now(), "today": date.today(),
            "badge": ({"apps": Application.query.filter_by(status="submitted").count(),
                       "msgs": ContactMessage.query.filter_by(read=False).count()}
                      if g.get("user") and g.user.role == "admin" else {})}


def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if role and g.user.role != role:
                abort(403)
            return fn(*a, **kw)
        return wrap
    return deco


def me_student():
    return Student.query.filter_by(user_id=g.user.id).first_or_404()


# ─────────────────────────── PUBLIC ───────────────────────────
ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}
ALLOWED_DOCS = ALLOWED_IMG | {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt"}


def save_upload(fs, folder="media", allowed=ALLOWED_IMG):
    """Save an uploaded file. Uses Supabase Storage when configured, otherwise static/uploads."""
    if not fs or not fs.filename:
        return ""
    ext = fs.filename.rsplit(".", 1)[-1].lower()
    if ext not in allowed:
        raise ValueError("Images must be PNG, JPG, WEBP or GIF." if allowed == ALLOWED_IMG
                         else "Files must be PDF, Word, PowerPoint, Excel, text or an image.")
    name = f"{folder}/{datetime.utcnow():%Y%m}/{uuid.uuid4().hex[:12]}.{ext}"
    data = fs.read()
    if SUPABASE_URL and SUPABASE_KEY:
        r = requests.post(f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{name}", data=data, timeout=60,
                          headers={**({"Authorization": f"Bearer {SUPABASE_KEY}"} if SUPABASE_KEY.startswith("eyJ") else {}), "apikey": SUPABASE_KEY,
                                   "Content-Type": fs.mimetype or "application/octet-stream", "x-upsert": "true"})
        if r.status_code >= 300:
            raise ValueError("Upload to Supabase Storage failed: " + r.text[:200])
        return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{name}"
    path = os.path.join(app.root_path, "static", "uploads", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return url_for("static", filename="uploads/" + name)


def slugify(text):
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:120] or "post"
    slug, i = base, 2
    while Post.query.filter_by(slug=slug).first():
        slug, i = f"{base}-{i}", i + 1
    return slug


def site_ctx():
    return {"footer_events": Post.query.filter(Post.kind == "event", Post.published == True,
                                               Post.event_date >= date.today()).order_by(Post.event_date).limit(3).all()}


@app.route("/")
def home():
    events = Post.query.filter(Post.kind == "event", Post.published == True, Post.event_date >= date.today()).order_by(Post.event_date).limit(4).all()
    news = Post.query.filter_by(kind="news", published=True).order_by(Post.created_at.desc()).limit(3).all()
    gallery = GalleryItem.query.order_by(GalleryItem.created_at.desc()).limit(8).all()
    tests = Testimonial.query.filter_by(show=True).all()
    years = max(datetime.now().year - int(get_setting("founded_year") or datetime.now().year), 1)
    return render_template("site/home.html", events=events, news=news, gallery=gallery, tests=tests, years=years, **site_ctx())


@app.route("/about")
def about():
    years = max(datetime.now().year - int(get_setting("founded_year") or datetime.now().year), 1)
    return render_template("site/about.html", years=years, **site_ctx())


@app.route("/academics")
def academics():
    subjects = Subject.query.order_by(Subject.name).all()
    return render_template("site/academics.html", subjects=subjects, **site_ctx())


@app.route("/facilities")
def facilities():
    return render_template("site/facilities.html", **site_ctx())


def admin_alert_email():
    e = get_setting("admin_notify_email") or get_setting("school_email")
    return e if valid_email(e) else None


@app.route("/admissions", methods=["GET", "POST"])
def admissions():
    if request.method == "POST":
        f = request.form
        if get_setting("admissions_open") != "1":
            flash("Admission is currently closed.", "error"); return redirect(url_for("admissions"))
        if f.get("website"):  # honeypot
            return redirect(url_for("admissions"))
        req = ["full_name", "gender", "class_sought", "parent_name", "phone", "email"]
        if any(not f.get(k, "").strip() for k in req):
            flash("Fill in all the required fields marked *.", "error")
            return render_template("site/admissions.html", form=f, levels=LEVELS, **site_ctx())
        if not valid_email(f["email"]):
            flash("Enter a valid email address for the parent or guardian. We send the admission updates there.", "error")
            return render_template("site/admissions.html", form=f, levels=LEVELS, **site_ctx())
        try:
            photo = save_upload(request.files.get("photo"), "applications")
        except ValueError as e:
            flash(str(e), "error")
            return render_template("site/admissions.html", form=f, levels=LEVELS, **site_ctx())
        n = Application.query.count() + 1
        app_no = f"{get_setting('school_short') or 'SCH'}/ADM/{datetime.now().year}/{n:04d}"
        while Application.query.filter_by(app_no=app_no).first():
            n += 1; app_no = f"{get_setting('school_short') or 'SCH'}/ADM/{datetime.now().year}/{n:04d}"
        a = Application(app_no=app_no, photo_url=photo, token=secrets.token_urlsafe(16), **{k: f.get(k, "").strip()[:500] for k in
                        ["full_name", "dob", "gender", "class_sought", "previous_school", "parent_name", "relationship",
                         "phone", "email", "address", "heard_from", "notes"]})
        db.session.add(a); db.session.commit()
        fee = form_fee()
        if setting_on("email_on_application"):
            send_email(a.email, f"Application received – {a.app_no}", "application_received", "admission", a=a,
                       form_fee=money(fee) if fee else None, button_text="Track your application",
                       button_url=abs_url("admission_status", t=a.token))
        if admin_alert_email():
            send_email(admin_alert_email(), f"New application: {a.full_name} ({a.class_sought})", "application_admin", "admin",
                       a=a, button_text="Open in admin", button_url=abs_url("admin_admission", aid=a.id))
        return redirect(url_for("application_done", token=a.token))
    return render_template("site/admissions.html", form={}, levels=LEVELS, **site_ctx())


def form_fee():
    try:
        return max(float(get_setting("form_fee") or 0), 0)
    except ValueError:
        return 0


@app.route("/admissions/done/<token>")
def application_done(token):
    a = Application.query.filter_by(token=token).first_or_404()
    return render_template("site/application_done.html", a=a, form_fee=form_fee(), **site_ctx())


@app.route("/admissions/status", methods=["GET", "POST"])
def admission_status():
    a = None
    if request.args.get("t"):
        a = Application.query.filter_by(token=request.args["t"]).first()
    if request.method == "POST":
        a = Application.query.filter(func.lower(Application.app_no) == request.form["app_no"].strip().lower(),
                                     Application.phone == request.form["phone"].strip()).first()
        if not a:
            flash("No application matches that number and phone. Check both and try again.", "error")
    return render_template("site/admission_status.html", a=a, form_fee=form_fee(), **site_ctx())


@app.route("/admissions/letter/<token>")
def admission_letter(token):
    a = Application.query.filter_by(token=token).first_or_404()
    if a.status != "admitted":
        abort(404)
    st = db.session.get(Student, a.student_id) if a.student_id else None
    return render_template("site/admission_letter.html", a=a, st=st)


@app.route("/admissions/pay/<token>", methods=["POST"])
def admission_pay(token):
    a = Application.query.filter_by(token=token).first_or_404()
    fee = form_fee()
    if a.form_paid or fee <= 0:
        return redirect(url_for("admission_status", t=a.token))
    p = Payment(application_id=a.id, session_id=current_session().id, term=current_term(), purpose="form",
                amount=fee, reference=new_ref("ADM"), payer_email=a.email)
    db.session.add(p); db.session.commit()
    return start_gateway(p, a.email, a.parent_name, a.phone, f"Admission form fee – {a.full_name}")


@app.route("/news")
def news_list():
    kind = request.args.get("kind", "")
    q = Post.query.filter_by(published=True)
    if kind in ("news", "event"):
        q = q.filter_by(kind=kind)
    posts = q.order_by(Post.created_at.desc()).all()
    upcoming = Post.query.filter(Post.kind == "event", Post.published == True, Post.event_date >= date.today()).order_by(Post.event_date).all()
    return render_template("site/news.html", posts=posts, upcoming=upcoming, kind=kind, **site_ctx())


@app.route("/news/<slug>")
def news_detail(slug):
    p = Post.query.filter_by(slug=slug, published=True).first_or_404()
    more = Post.query.filter(Post.id != p.id, Post.published == True).order_by(Post.created_at.desc()).limit(3).all()
    return render_template("site/news_detail.html", p=p, more=more, **site_ctx())


@app.route("/gallery")
def gallery():
    items = GalleryItem.query.order_by(GalleryItem.created_at.desc()).all()
    cats = sorted({i.category for i in items})
    return render_template("site/gallery.html", items=items, cats=cats, **site_ctx())


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        f = request.form
        if f.get("website"):
            return redirect(url_for("contact"))
        if not f.get("name", "").strip() or not f.get("message", "").strip():
            flash("Enter your name and a message.", "error")
        else:
            m = ContactMessage(name=f["name"].strip()[:128], email=f.get("email", "")[:128], phone=f.get("phone", "")[:32],
                               subject=f.get("subject", "General")[:64], message=f["message"].strip()[:3000])
            db.session.add(m); db.session.commit()
            if valid_email(m.email):
                send_email(m.email, f"We received your message – {get_setting('school_name')}", "contact_autoreply", "contact", m=m)
            if admin_alert_email():
                send_email(admin_alert_email(), f"Website message: {m.subject} from {m.name}", "contact_admin", "admin", m=m,
                           button_text="Open messages", button_url=abs_url("admin_messages"))
            flash("Message sent. The school office will reply within one working day.", "success")
            return redirect(url_for("contact"))
    return render_template("site/contact.html", **site_ctx())


@app.route("/portal")
def portal():
    if g.user:
        return redirect(url_for(f"{g.user.role}_dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter(func.lower(User.username) == request.form["username"].strip().lower()).first()
        if u and u.active and u.check_password(request.form["password"]):
            session.clear()
            session["uid"] = u.id
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for(f"{u.role}_dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/password", methods=["GET", "POST"])
@login_required()
def change_password():
    if request.method == "POST":
        if not g.user.check_password(request.form["old"]):
            flash("Current password is wrong.", "error")
        elif len(request.form["new"]) < 6 or request.form["new"] != request.form["confirm"]:
            flash("New passwords must match and be at least 6 characters.", "error")
        else:
            g.user.set_password(request.form["new"]); db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for(f"{g.user.role}_dashboard"))
    return render_template("password.html")


def check_result_with_pin(reg_no, serial, pin, session_id, term):
    """Validates card + student. Returns (report, error)."""
    st = Student.query.filter(func.lower(Student.reg_no) == reg_no.strip().lower()).first()
    if not st:
        return None, "Registration number not found."
    card = ScratchCard.query.filter_by(pin=pin.strip(), serial=serial.strip().upper()).first()
    if not card:
        return None, "Invalid PIN or serial number."
    sess = db.session.get(AcademicSession, int(session_id))
    if not sess or term not in TERMS:
        return None, "Select a valid session and term."
    if card.student_id and card.student_id != st.id:
        return None, "This PIN has already been used by another student."
    if card.session_id and (card.session_id != sess.id or card.term != term):
        s2 = db.session.get(AcademicSession, card.session_id)
        return None, f"This PIN is only valid for {card.term}, {s2.name if s2 else ''}."
    if card.used_count >= card.max_uses:
        return None, f"This PIN has reached its maximum of {card.max_uses} uses."
    if not is_published(st.class_id, sess.id, term):
        return None, "Results for your class have not been published yet."
    if get_setting("block_result_if_owing") == "1":
        _, total, paid, bal = fee_summary(st, sess, term)
        if bal > 0:
            return None, f"Outstanding school fees of {money(bal)}. Please clear your fees to view this result."
    card.student_id = st.id
    card.session_id, card.term = sess.id, term
    card.used_count += 1
    db.session.commit()
    rep = build_report(st, sess, term)
    rep["card"] = card
    return rep, None


@app.route("/result", methods=["GET", "POST"])
def public_result():
    if request.method == "POST":
        f = request.form
        rep, err = check_result_with_pin(f["reg_no"], f["serial"], f["pin"], f["session_id"], f["term"])
        if err:
            flash(err, "error")
        else:
            return render_template("report_card.html", r=rep)
    return render_template("result_check.html", sessions=AcademicSession.query.order_by(AcademicSession.name.desc()).all())


# ─────────────────────────── PAYMENTS (Flutterwave) ───────────────────────────
FLW_API = "https://api.flutterwave.com/v3"


def new_ref(prefix="SCH"):
    return f"{prefix}-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(5).upper()}"


def flw(method, path, **kw):
    try:
        r = requests.request(method, FLW_API + path, timeout=30,
                             headers={"Authorization": f"Bearer {FLW_SECRET}", "Content-Type": "application/json"}, **kw)
        return r.json()
    except (requests.RequestException, ValueError) as e:
        return {"status": "error", "message": str(e)}


def purpose_label(p):
    return {"fees": "School fees", "pin": "Result checker PIN", "form": "Admission form fee"}.get(p.purpose, p.purpose)


def remember_paid(ref):
    refs = session.get("paid_refs", [])[-9:]
    session["paid_refs"] = refs + [ref]


def start_gateway(p, email, name, phone, title):
    """Send the payer to Flutterwave checkout (or the demo screen when no key is set)."""
    if not FLW_SECRET:
        return redirect(url_for("pay_demo", ref=p.reference))
    logo = abs_url("static", filename="img/crest.png")
    res = flw("POST", "/payments", json={
        "tx_ref": p.reference, "amount": round(p.amount, 2), "currency": "NGN",
        "redirect_url": abs_url("pay_callback"),
        "customer": {"email": email, "name": name or "Parent", "phonenumber": phone or ""},
        "customizations": {"title": get_setting("school_name"), "description": title, "logo": logo},
        "meta": {"purpose": p.purpose, "payment_id": p.id},
    })
    if res.get("status") == "success" and (res.get("data") or {}).get("link"):
        return redirect(res["data"]["link"])
    p.status = "failed"; p.note = str(res.get("message"))[:250]; db.session.commit()
    flash("Could not start the payment: " + str(res.get("message") or "please try again."), "error")
    return redirect(request.referrer or url_for("home"))


def verify_flw(p, tx_id=None):
    """Ask Flutterwave whether this payment really succeeded."""
    res = flw("GET", f"/transactions/{tx_id}/verify") if tx_id else \
        flw("GET", "/transactions/verify_by_reference", params={"tx_ref": p.reference})
    d = res.get("data") or {}
    ok = (res.get("status") == "success" and d.get("status") == "successful" and d.get("tx_ref") == p.reference
          and d.get("currency") == "NGN" and float(d.get("amount") or 0) >= round(p.amount, 2))
    if ok:
        p.gateway_id = str(d.get("id") or "")[:64]
    return ok, d


def finalize_payment(p, channel=None):
    """Mark payment successful (safe to call twice). Creates PINs, marks form fees, emails the receipt."""
    if p.status == "success":
        return
    p.status, p.paid_at = "success", datetime.utcnow()
    if channel:
        p.channel = channel
    if p.purpose == "pin" and not p.card_id:
        c = new_card(p.session_id, p.term, "ONLINE", source="online")
        c.student_id = p.student_id
        db.session.flush()
        p.card_id = c.id
    if p.purpose == "form" and p.application:
        p.application.form_paid = True
        if p.application.status == "submitted":
            p.application.status = "reviewing"
    db.session.commit()
    if setting_on("email_on_payment"):
        to, name, bal = p.payer_email, "Parent", None
        if p.student:
            to = guardian_email(p.student) or to
            name = p.student.guardian_name or p.student.user.full_name
            sess = db.session.get(AcademicSession, p.session_id)
            if p.purpose == "fees" and sess:
                bal = fee_summary(p.student, sess, p.term)[3]
        elif p.application:
            to, name = p.application.email, p.application.parent_name
        if to:
            send_email(to, f"Payment receipt {p.reference} – {money(p.amount)}", "payment_receipt", "payment",
                       p=p, name=name, purpose=purpose_label(p), balance=bal,
                       button_text="View receipt", button_url=abs_url("receipt", ref=p.reference))


def payable_student():
    """The student a fee/PIN payment is for: the logged-in student, or a parent's chosen child."""
    if g.user.role == "student":
        return me_student()
    if g.user.role == "parent":
        st = db.session.get(Student, int(request.form.get("student_id") or 0))
        if st and st.parent_id == g.user.id:
            return st
    abort(403)


@app.route("/pay/start", methods=["POST"])
@login_required()
def pay_start():
    st, sess, term = payable_student(), current_session(), current_term()
    back = url_for("parent_dashboard") if g.user.role == "parent" else url_for("student_fees")
    purpose = request.form.get("purpose", "fees")
    if purpose == "pin":
        amount = float(get_setting("pin_price") or 0)
    else:
        purpose = "fees"
        _, total, paid, bal = fee_summary(st, sess, term)
        try:
            amount = round(float(request.form.get("amount") or bal), 2)
        except ValueError:
            amount = 0
        if amount <= 0 or amount > bal:
            flash(f"Enter an amount between ₦1 and the balance of {money(bal)}.", "error")
            return redirect(back)
    email = (guardian_email(st) if g.user.role == "parent" else None) or g.user.email or guardian_email(st) \
        or f"{re.sub(r'[^a-z0-9]', '', st.reg_no.lower())}@students.{(get_setting('school_short') or 'school').lower()}.ng"
    p = Payment(student_id=st.id, session_id=sess.id, term=term, purpose=purpose, amount=amount, reference=new_ref(),
                payer_email=email, paid_by_user_id=g.user.id)
    db.session.add(p); db.session.commit()
    return start_gateway(p, email, g.user.full_name, st.guardian_phone or g.user.phone,
                         f"{purpose_label(p)} – {st.user.full_name} ({term})")


@app.route("/pay/demo/<ref>", methods=["GET", "POST"])
def pay_demo(ref):
    if FLW_SECRET:
        abort(404)
    p = Payment.query.filter_by(reference=ref).first_or_404()
    if request.method == "POST":
        finalize_payment(p, "demo")
        remember_paid(ref)
        return redirect(url_for("receipt", ref=ref))
    return render_template("student/pay_demo.html", p=p, label=purpose_label(p))


@app.route("/pay/callback")
def pay_callback():
    ref = request.args.get("tx_ref", "")
    p = Payment.query.filter_by(reference=ref).first_or_404()
    if request.args.get("status") in ("successful", "completed") or p.status == "success":
        ok, _ = verify_flw(p, request.args.get("transaction_id"))
        if ok or p.status == "success":
            finalize_payment(p, "flutterwave")
            remember_paid(ref)
            flash("Payment successful. A receipt has been sent by email.", "success")
            return redirect(url_for("receipt", ref=ref))
    if p.status == "pending":
        p.status = "failed" if request.args.get("status") == "cancelled" else "pending"
        db.session.commit()
    flash("The payment was not completed. You have not been charged.", "error")
    if p.purpose == "form" and p.application:
        return redirect(url_for("admission_status", t=p.application.token))
    return redirect(url_for("portal"))


@app.route("/flutterwave/webhook", methods=["POST"])
def flutterwave_webhook():
    if not FLW_SECRET or not FLW_HASH or not hmac.compare_digest(request.headers.get("verif-hash", ""), FLW_HASH):
        abort(401)
    ev = request.get_json(silent=True) or {}
    d = ev.get("data") or {}
    if (ev.get("event") == "charge.completed" or ev.get("event.type")) and d.get("tx_ref"):
        p = Payment.query.filter_by(reference=d["tx_ref"]).first()
        if p and p.status != "success":
            ok, _ = verify_flw(p, d.get("id"))
            if ok:
                finalize_payment(p, "flutterwave")
    return "ok"


@app.route("/receipt/<ref>")
def receipt(ref):
    p = Payment.query.filter_by(reference=ref).first_or_404()
    allowed = ref in session.get("paid_refs", [])
    if g.user:
        if g.user.role == "admin":
            allowed = True
        elif p.student and g.user.role == "student" and p.student.user_id == g.user.id:
            allowed = True
        elif p.student and g.user.role == "parent" and p.student.parent_id == g.user.id:
            allowed = True
    if not allowed:
        abort(403) if g.user else abort(404)
    sess = db.session.get(AcademicSession, p.session_id)
    bal = fee_summary(p.student, sess, p.term)[3] if (p.purpose == "fees" and sess and p.student) else None
    return render_template("receipt.html", p=p, sess=sess, bal=bal, label=purpose_label(p))


# ─────────────────────────── STUDENT ───────────────────────────
@app.route("/student")
@login_required("student")
def student_dashboard():
    st, sess, term = me_student(), current_session(), current_term()
    items, total, paid, bal = fee_summary(st, sess, term) if sess else ([], 0, 0, 0)
    news = Announcement.query.filter(Announcement.audience.in_(["all", "students"])).order_by(Announcement.created_at.desc()).limit(5).all()
    pubs = Publication.query.filter_by(class_id=st.class_id).order_by(Publication.published_at.desc()).all() if st.class_id else []
    cards = ScratchCard.query.filter_by(student_id=st.id).order_by(ScratchCard.created_at.desc()).all()
    wd = date.today().weekday()
    today_slots = []
    if st.class_id and wd < 5:
        grid = timetable_grid(st.class_id)
        today_slots = [(periods()[i], grid.get((wd, i))) for i in range(len(periods())) if grid.get((wd, i))]
    return render_template("student/dashboard.html", st=st, total=total, paid=paid, bal=bal, news=news, pubs=pubs, cards=cards,
                           sessions={s.id: s.name for s in AcademicSession.query.all()},
                           att=attendance_summary(st, sess.id, term), hw=class_homework(st.class_id, 4) if st.class_id else [],
                           today_slots=today_slots, today=date.today())


@app.route("/student/fees")
@login_required("student")
def student_fees():
    st, sess, term = me_student(), current_session(), current_term()
    items, total, paid, bal = fee_summary(st, sess, term)
    history = Payment.query.filter_by(student_id=st.id).order_by(Payment.created_at.desc()).all()
    return render_template("student/fees.html", st=st, items=items, total=total, paid=paid, bal=bal, history=history,
                           sessions={s.id: s.name for s in AcademicSession.query.all()})


@app.route("/student/result", methods=["GET", "POST"])
@login_required("student")
def student_result():
    st = me_student()
    if request.method == "POST":
        f = request.form
        rep, err = check_result_with_pin(st.reg_no, f["serial"], f["pin"], f["session_id"], f["term"])
        if err:
            flash(err, "error")
        else:
            return render_template("report_card.html", r=rep)
    sessions = AcademicSession.query.order_by(AcademicSession.name.desc()).all()
    cards = ScratchCard.query.filter_by(student_id=st.id).order_by(ScratchCard.created_at.desc()).all()
    return render_template("student/result.html", st=st, sessions=sessions, cards=cards)


@app.route("/student/profile")
@login_required("student")
def student_profile():
    return render_template("student/profile.html", st=me_student())


# ─────────────────────────── TEACHER ───────────────────────────
@app.route("/teacher")
@login_required("teacher")
def teacher_dashboard():
    assigns = ClassSubject.query.filter_by(teacher_id=g.user.id).all()
    form_classes = SchoolClass.query.filter_by(form_teacher_id=g.user.id).all()
    sess, term = current_session(), current_term()
    progress = {}
    for a in assigns:
        n = Student.query.filter_by(class_id=a.class_id, status="active").count()
        done = Score.query.filter_by(class_id=a.class_id, subject_id=a.subject_id, session_id=sess.id if sess else 0, term=term).count()
        progress[a.id] = (done, n)
    news = Announcement.query.filter(Announcement.audience.in_(["all", "teachers"])).order_by(Announcement.created_at.desc()).limit(5).all()
    return render_template("teacher/dashboard.html", assigns=assigns, form_classes=form_classes, progress=progress, news=news)


def can_edit_cs(cs):
    return g.user.role == "admin" or cs.teacher_id == g.user.id


def parse_score(v, mx):
    try:
        x = float(v) if str(v).strip() != "" else 0.0
    except ValueError:
        raise ValueError(f"'{v}' is not a number")
    if x < 0 or x > mx:
        raise ValueError(f"{x} is outside 0–{mx:g}")
    return x


@app.route("/scores/<int:cs_id>", methods=["GET", "POST"])
@login_required()
def enter_scores(cs_id):
    cs = db.get_or_404(ClassSubject, cs_id)
    if not can_edit_cs(cs):
        abort(403)
    sess, term = current_session(), current_term()
    if not sess:
        flash("Admin must create an academic session first.", "error")
        return redirect(url_for(f"{g.user.role}_dashboard"))
    locked = is_published(cs.class_id, sess.id, term) and g.user.role != "admin"
    students = Student.query.filter_by(class_id=cs.class_id, status="active").join(User, Student.user_id == User.id).order_by(User.full_name).all()
    mx = {k: float(get_setting(k + "_max")) for k in ("ca1", "ca2", "exam")}
    if request.method == "POST":
        if locked:
            flash("Results are published and locked. Ask the admin to unpublish to make changes.", "error")
            return redirect(request.url)
        errors = []
        for st in students:
            vals = [request.form.get(f"{k}_{st.id}", "") for k in ("ca1", "ca2", "exam")]
            if all(v.strip() == "" for v in vals):
                continue
            try:
                c1, c2, ex = (parse_score(v, mx[k]) for v, k in zip(vals, ("ca1", "ca2", "exam")))
            except ValueError as e:
                errors.append(f"{st.user.full_name}: {e}")
                continue
            save_score(st, cs, sess, term, c1, c2, ex)
        db.session.commit()
        flash("Scores saved." if not errors else "Saved, with errors: " + "; ".join(errors), "error" if errors else "success")
        return redirect(request.url)
    existing = {s.student_id: s for s in Score.query.filter_by(subject_id=cs.subject_id, class_id=cs.class_id, session_id=sess.id, term=term)}
    return render_template("teacher/scores.html", cs=cs, students=students, existing=existing, mx=mx, locked=locked, grade_for=grade_for)


def save_score(st, cs, sess, term, c1, c2, ex):
    sc = Score.query.filter_by(student_id=st.id, subject_id=cs.subject_id, session_id=sess.id, term=term).first()
    if not sc:
        sc = Score(student_id=st.id, subject_id=cs.subject_id, class_id=cs.class_id, session_id=sess.id, term=term)
        db.session.add(sc)
    sc.ca1, sc.ca2, sc.exam, sc.class_id, sc.updated_by = c1, c2, ex, cs.class_id, g.user.id


@app.route("/scores/<int:cs_id>/template.csv")
@login_required()
def scores_template(cs_id):
    cs = db.get_or_404(ClassSubject, cs_id)
    if not can_edit_cs(cs):
        abort(403)
    sess, term = current_session(), current_term()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["reg_no", "full_name", "ca1", "ca2", "exam"])
    for st in Student.query.filter_by(class_id=cs.class_id, status="active").join(User, Student.user_id == User.id).order_by(User.full_name):
        sc = Score.query.filter_by(student_id=st.id, subject_id=cs.subject_id, session_id=sess.id, term=term).first()
        w.writerow([st.reg_no, st.user.full_name, sc.ca1 if sc else "", sc.ca2 if sc else "", sc.exam if sc else ""])
    fn = f"{cs.school_class.name}_{cs.subject.name}_{term}.csv".replace(" ", "_")
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename={fn}"})


@app.route("/scores/<int:cs_id>/upload", methods=["POST"])
@login_required()
def scores_upload(cs_id):
    cs = db.get_or_404(ClassSubject, cs_id)
    if not can_edit_cs(cs):
        abort(403)
    sess, term = current_session(), current_term()
    if is_published(cs.class_id, sess.id, term) and g.user.role != "admin":
        flash("Results are published and locked.", "error")
        return redirect(url_for("enter_scores", cs_id=cs_id))
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".csv"):
        flash("Upload a .csv file (use the template).", "error")
        return redirect(url_for("enter_scores", cs_id=cs_id))
    mx = {k: float(get_setting(k + "_max")) for k in ("ca1", "ca2", "exam")}
    rows = csv.DictReader(io.StringIO(f.read().decode("utf-8-sig")))
    ok, errors = 0, []
    for i, row in enumerate(rows, 2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        st = Student.query.filter(func.lower(Student.reg_no) == row.get("reg_no", "").lower(), Student.class_id == cs.class_id).first()
        if not st:
            errors.append(f"row {i}: reg no '{row.get('reg_no')}' not in this class"); continue
        if all(row.get(k, "") == "" for k in ("ca1", "ca2", "exam")):
            continue
        try:
            c1, c2, ex = (parse_score(row.get(k, ""), mx[k]) for k in ("ca1", "ca2", "exam"))
        except ValueError as e:
            errors.append(f"row {i}: {e}"); continue
        save_score(st, cs, sess, term, c1, c2, ex); ok += 1
    db.session.commit()
    flash(f"{ok} student scores uploaded." + (" Problems: " + "; ".join(errors[:10]) if errors else ""), "error" if errors else "success")
    return redirect(url_for("enter_scores", cs_id=cs_id))


@app.route("/remarks/<int:class_id>", methods=["GET", "POST"])
@login_required()
def class_remarks(class_id):
    cl = db.get_or_404(SchoolClass, class_id)
    if g.user.role != "admin" and cl.form_teacher_id != g.user.id:
        abort(403)
    sess, term = current_session(), current_term()
    students = Student.query.filter_by(class_id=class_id, status="active").join(User, Student.user_id == User.id).order_by(User.full_name).all()
    if request.method == "POST":
        for st in students:
            r = Remark.query.filter_by(student_id=st.id, session_id=sess.id, term=term).first()
            if not r:
                r = Remark(student_id=st.id, session_id=sess.id, term=term); db.session.add(r)
            f = request.form
            r.teacher_comment = f.get(f"tc_{st.id}", "")[:500]
            if g.user.role == "admin":
                r.principal_comment = f.get(f"pc_{st.id}", "")[:500]
            for fld in ("days_present", "days_open"):
                try:
                    setattr(r, fld, int(f.get(f"{fld}_{st.id}") or 0))
                except ValueError:
                    pass
            for fld in ("punctuality", "neatness", "conduct"):
                setattr(r, fld, f.get(f"{fld}_{st.id}", "")[:2])
        db.session.commit()
        flash("Remarks saved.", "success")
        return redirect(request.url)
    data, _, _, _ = class_results(class_id, sess.id, term)
    remarks = {r.student_id: r for r in Remark.query.filter_by(session_id=sess.id, term=term).filter(Remark.student_id.in_([s.id for s in students] or [0]))}
    return render_template("teacher/remarks.html", cl=cl, students=students, remarks=remarks, data=data)


@app.route("/broadsheet/<int:class_id>")
@login_required()
def broadsheet(class_id):
    cl = db.get_or_404(SchoolClass, class_id)
    if g.user.role == "student":
        abort(403)
    if g.user.role == "teacher" and cl.form_teacher_id != g.user.id and not ClassSubject.query.filter_by(class_id=class_id, teacher_id=g.user.id).first():
        abort(403)
    sess = db.session.get(AcademicSession, int(request.args.get("session_id") or current_session().id))
    term = request.args.get("term") or current_term()
    data, stats, _, n = class_results(class_id, sess.id, term)
    subjects = [cs.subject for cs in ClassSubject.query.filter_by(class_id=class_id).join(Subject).order_by(Subject.name)]
    rows = sorted(data.values(), key=lambda d: (d.get("position") or 9999, d["student"].user.full_name))
    if request.args.get("format") == "csv":
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Reg No", "Name"] + [s.name for s in subjects] + ["Total", "Average", "Position"])
        for d in rows:
            m = {x.subject_id: x.total for x in d["scores"]}
            w.writerow([d["student"].reg_no, d["student"].user.full_name] + [m.get(s.id, "") for s in subjects] + [d["total"], d["average"], d.get("position", "")])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=broadsheet_{cl.name}_{term}.csv".replace(" ", "_")})
    return render_template("broadsheet.html", cl=cl, sess=sess, term=term, subjects=subjects, rows=rows, stats=stats,
                           sessions=AcademicSession.query.order_by(AcademicSession.name.desc()).all(),
                           published=is_published(class_id, sess.id, term))


# ─────────────────────────── ATTENDANCE ───────────────────────────
def can_mark_class(cl):
    return g.user.role == "admin" or (g.user.role == "teacher" and cl.form_teacher_id == g.user.id)


@app.route("/attendance/<int:class_id>", methods=["GET", "POST"])
@login_required()
def attendance_register(class_id):
    cl = db.get_or_404(SchoolClass, class_id)
    if not can_mark_class(cl):
        abort(403)
    sess, term = current_session(), current_term()
    try:
        day = datetime.strptime(request.values.get("day", ""), "%Y-%m-%d").date()
    except ValueError:
        day = date.today()
    if day > date.today():
        day = date.today()
    students = Student.query.filter_by(class_id=class_id, status="active").join(User, Student.user_id == User.id).order_by(User.full_name).all()
    existing = {a.student_id: a for a in Attendance.query.filter_by(class_id=class_id, day=day)}
    if request.method == "POST":
        alerts = []
        for st in students:
            v = request.form.get(f"s_{st.id}")
            if v not in ("P", "A", "L"):
                continue
            rec = existing.get(st.id) or Attendance.query.filter_by(student_id=st.id, day=day).first()
            before = rec.status if rec else None
            if not rec:
                rec = Attendance(student_id=st.id, day=day); db.session.add(rec)
            rec.class_id, rec.session_id, rec.term, rec.status, rec.marked_by = class_id, sess.id, term, v, g.user.id
            if v in ("A", "L") and before != v:
                alerts.append((st, v))
        db.session.commit()
        n = 0
        if setting_on("email_on_absence") and day == date.today():
            for st, v in alerts:
                to = guardian_email(st)
                if to and send_email(to, f"{'Absence' if v == 'A' else 'Late arrival'} notice – {st.user.full_name}", "absence",
                                     "attendance", st=st, status=v, day=day):
                    n += 1
        flash(f"Attendance saved for {day.strftime('%A %d %B')}." + (f" {n} parent(s) notified." if n else ""), "success")
        return redirect(url_for("attendance_register", class_id=class_id, day=day.isoformat()))
    week = []
    start = day - timedelta(days=day.weekday())
    for i in range(5):
        d = start + timedelta(days=i)
        week.append((d, Attendance.query.filter_by(class_id=class_id, day=d).count()))
    summary = {st.id: attendance_summary(st, sess.id, term) for st in students}
    return render_template("attendance/register.html", cl=cl, day=day, students=students, existing=existing,
                           week=week, summary=summary, timedelta=timedelta)


@app.route("/attendance/student/<int:sid>")
@login_required()
def attendance_student(sid):
    st = db.get_or_404(Student, sid)
    ok = g.user.role == "admin" or (g.user.role == "student" and st.user_id == g.user.id) or \
        (g.user.role == "parent" and st.parent_id == g.user.id) or \
        (g.user.role == "teacher" and st.school_class and st.school_class.form_teacher_id == g.user.id)
    if not ok:
        abort(403)
    sess, term = current_session(), current_term()
    recs = Attendance.query.filter_by(student_id=sid, session_id=sess.id, term=term).order_by(Attendance.day.desc()).all()
    return render_template("attendance/student.html", st=st, recs=recs, summary=attendance_summary(st, sess.id, term))


@app.route("/admin/attendance")
@login_required("admin")
def admin_attendance():
    d = date.today()
    rows = []
    for cl in SchoolClass.query.order_by(SchoolClass.name).all():
        n = Student.query.filter_by(class_id=cl.id, status="active").count()
        marked = Attendance.query.filter_by(class_id=cl.id, day=d).count()
        absent = Attendance.query.filter_by(class_id=cl.id, day=d, status="A").count()
        rows.append((cl, n, marked, absent))
    return render_template("attendance/overview.html", rows=rows, day=d)


# ─────────────────────────── HOMEWORK ───────────────────────────
@app.route("/homework/<int:cs_id>", methods=["GET", "POST"])
@login_required()
def homework_manage(cs_id):
    cs = db.get_or_404(ClassSubject, cs_id)
    if not can_edit_cs(cs):
        abort(403)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete":
            hw = db.get_or_404(Homework, int(f["id"]))
            if hw.class_subject_id == cs.id:
                db.session.delete(hw); db.session.commit(); flash("Homework removed.", "success")
            return redirect(request.url)
        if not f.get("title", "").strip():
            flash("Give the homework a title.", "error"); return redirect(request.url)
        try:
            file_url = save_upload(request.files.get("file"), "homework", ALLOWED_DOCS)
        except ValueError as e:
            flash(str(e), "error"); return redirect(request.url)
        try:
            due = datetime.strptime(f.get("due_date", ""), "%Y-%m-%d").date()
        except ValueError:
            due = None
        hw = Homework(class_subject_id=cs.id, title=f["title"].strip()[:160], details=f.get("details", "").strip(),
                      due_date=due, file_url=file_url, teacher_id=g.user.id)
        db.session.add(hw); db.session.commit()
        n = 0
        if setting_on("email_on_homework") and f.get("notify"):
            for st in Student.query.filter_by(class_id=cs.class_id, status="active"):
                to = guardian_email(st)
                if to and send_email(to, f"Homework: {hw.title} ({cs.subject.name})", "homework", "homework", st=st, hw=hw,
                                     button_text="Open the portal", button_url=abs_url("login")):
                    n += 1
        flash("Homework posted. Students can see it on their portal." + (f" {n} parent(s) emailed." if n else ""), "success")
        return redirect(request.url)
    items = Homework.query.filter_by(class_subject_id=cs.id).order_by(Homework.created_at.desc()).all()
    return render_template("homework/manage.html", cs=cs, items=items)


def class_homework(class_id, limit=None):
    q = Homework.query.join(ClassSubject).filter(ClassSubject.class_id == class_id).order_by(Homework.created_at.desc())
    return q.limit(limit).all() if limit else q.all()


# ─────────────────────────── TIMETABLE ───────────────────────────
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def periods():
    return [p.strip() for p in (get_setting("periods") or "").split(",") if p.strip()]


def timetable_grid(class_id):
    grid = {}
    for t in TimetableSlot.query.filter_by(class_id=class_id):
        grid[(t.day, t.period)] = t
    return grid


@app.route("/admin/timetable", methods=["GET", "POST"])
@login_required("admin")
def admin_timetable():
    classes = SchoolClass.query.order_by(SchoolClass.name).all()
    cid = int(request.values.get("class_id") or (classes[0].id if classes else 0))
    if request.method == "POST" and cid:
        if request.form.get("periods"):
            set_setting("periods", request.form["periods"].strip())
        TimetableSlot.query.filter_by(class_id=cid).delete()
        for d in range(5):
            for pi in range(len(periods())):
                v = request.form.get(f"c_{d}_{pi}", "")
                if not v:
                    continue
                if v.startswith("s:"):
                    db.session.add(TimetableSlot(class_id=cid, day=d, period=pi, subject_id=int(v[2:])))
                else:
                    db.session.add(TimetableSlot(class_id=cid, day=d, period=pi, label=v[2:][:32]))
        db.session.commit(); flash("Timetable saved.", "success")
        return redirect(url_for("admin_timetable", class_id=cid))
    subjects = [cs.subject for cs in ClassSubject.query.filter_by(class_id=cid).join(Subject).order_by(Subject.name)] if cid else []
    return render_template("timetable/edit.html", classes=classes, cid=cid, grid=timetable_grid(cid), subjects=subjects,
                           periods=periods(), days=DAYS)


@app.route("/timetable/<int:class_id>")
@login_required()
def timetable_view(class_id):
    cl = db.get_or_404(SchoolClass, class_id)
    teachers = {cs.subject_id: cs.teacher for cs in ClassSubject.query.filter_by(class_id=class_id)}
    return render_template("timetable/view.html", cl=cl, grid=timetable_grid(class_id), periods=periods(), days=DAYS,
                           teachers=teachers, today=date.today().weekday())


@app.route("/teacher/timetable")
@login_required("teacher")
def teacher_timetable():
    mine = {(cs.class_id, cs.subject_id) for cs in ClassSubject.query.filter_by(teacher_id=g.user.id)}
    grid = {}
    for t in TimetableSlot.query.filter(TimetableSlot.subject_id.isnot(None)):
        if (t.class_id, t.subject_id) in mine:
            grid.setdefault((t.day, t.period), []).append(t)
    names = {c.id: c.name for c in SchoolClass.query.all()}
    return render_template("timetable/teacher.html", grid=grid, periods=periods(), days=DAYS, names=names)


# ─────────────────────────── STUDENT EXTRAS ───────────────────────────
@app.route("/student/homework")
@login_required("student")
def student_homework():
    st = me_student()
    return render_template("homework/list.html", st=st, items=class_homework(st.class_id) if st.class_id else [], today=date.today())


@app.route("/student/timetable")
@login_required("student")
def student_timetable():
    st = me_student()
    if not st.class_id:
        flash("You are not in a class yet.", "error"); return redirect(url_for("student_dashboard"))
    return redirect(url_for("timetable_view", class_id=st.class_id))


# ─────────────────────────── PARENT PORTAL ───────────────────────────
def my_children():
    return Student.query.filter_by(parent_id=g.user.id).join(User, Student.user_id == User.id).order_by(User.full_name).all()


def child_or_403(sid):
    st = db.get_or_404(Student, sid)
    if st.parent_id != g.user.id:
        abort(403)
    return st


@app.route("/parent")
@login_required("parent")
def parent_dashboard():
    sess, term = current_session(), current_term()
    kids = []
    for st in my_children():
        items, total, paid, bal = fee_summary(st, sess, term)
        kids.append({"st": st, "total": total, "paid": paid, "bal": bal,
                     "att": attendance_summary(st, sess.id, term),
                     "hw": class_homework(st.class_id, 3) if st.class_id else [],
                     "published": [p for p in Publication.query.filter_by(class_id=st.class_id).order_by(Publication.published_at.desc())] if st.class_id else []})
    news = Announcement.query.filter(Announcement.audience.in_(["all", "students"])).order_by(Announcement.created_at.desc()).limit(4).all()
    return render_template("parent/dashboard.html", kids=kids, news=news, sessions={s.id: s.name for s in AcademicSession.query.all()})


@app.route("/parent/child/<int:sid>")
@login_required("parent")
def parent_child(sid):
    st = child_or_403(sid)
    sess, term = current_session(), current_term()
    items, total, paid, bal = fee_summary(st, sess, term)
    pays = Payment.query.filter_by(student_id=st.id).order_by(Payment.created_at.desc()).all()
    cards = ScratchCard.query.filter_by(student_id=st.id).order_by(ScratchCard.created_at.desc()).all()
    return render_template("parent/child.html", st=st, items=items, total=total, paid=paid, bal=bal, pays=pays, cards=cards,
                           att=attendance_summary(st, sess.id, term), hw=class_homework(st.class_id, 10) if st.class_id else [],
                           sessions={s.id: s.name for s in AcademicSession.query.all()}, today=date.today())


@app.route("/parent/result/<int:sid>", methods=["GET", "POST"])
@login_required("parent")
def parent_result(sid):
    st = child_or_403(sid)
    if request.method == "POST":
        f = request.form
        rep, err = check_result_with_pin(st.reg_no, f["serial"], f["pin"], f["session_id"], f["term"])
        if err:
            flash(err, "error")
        else:
            return render_template("report_card.html", r=rep)
    sessions = AcademicSession.query.order_by(AcademicSession.name.desc()).all()
    cards = ScratchCard.query.filter_by(student_id=st.id).order_by(ScratchCard.created_at.desc()).all()
    return render_template("student/result.html", st=st, sessions=sessions, cards=cards, parent_view=True)


# ─────────────────────────── ID CARDS ───────────────────────────
@app.route("/admin/idcards")
@login_required("admin")
def admin_idcards():
    if request.args.get("sid"):
        students = [db.get_or_404(Student, int(request.args["sid"]))]
    elif request.args.get("class_id"):
        students = Student.query.filter_by(class_id=int(request.args["class_id"]), status="active").join(User, Student.user_id == User.id).order_by(User.full_name).all()
    else:
        return render_template("admin/idcards_pick.html", classes=SchoolClass.query.order_by(SchoolClass.name).all())
    return render_template("admin/idcards.html", students=students)


# ─────────────────────────── ADMIN ───────────────────────────
admin = login_required("admin")


@app.route("/admin")
@admin
def admin_dashboard():
    sess, term = current_session(), current_term()
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter_by(
        status="success", session_id=sess.id if sess else 0, term=term).scalar()
    stats = {"students": Student.query.filter_by(status="active").count(),
             "teachers": User.query.filter_by(role="teacher", active=True).count(),
             "classes": SchoolClass.query.count(), "subjects": Subject.query.count(),
             "revenue": revenue,
             "pins_unused": ScratchCard.query.filter_by(used_count=0).count(),
             "pins_used": ScratchCard.query.filter(ScratchCard.used_count > 0).count(),
             "published": Publication.query.filter_by(session_id=sess.id if sess else 0, term=term).count()}
    # fees owed across students
    owing, expected = 0, 0
    if sess:
        for st in Student.query.filter_by(status="active").all():
            _, t, p, b = fee_summary(st, sess, term)
            expected += t; owing += b
    stats["expected"], stats["owing"] = expected, owing
    recent = Payment.query.filter_by(status="success").order_by(Payment.paid_at.desc()).limit(8).all()
    by_class = db.session.query(SchoolClass.name, func.count(Student.id)).outerjoin(
        Student, (Student.class_id == SchoolClass.id) & (Student.status == "active")).group_by(SchoolClass.id, SchoolClass.name).order_by(SchoolClass.name).all()
    return render_template("admin/dashboard.html", stats=stats, recent=recent, by_class=by_class)


@app.route("/admin/settings", methods=["GET", "POST"])
@admin
def admin_settings():
    if request.method == "POST":
        checkboxes = {"general": ["block_result_if_owing"], "website": ["admissions_open"],
                      "notify": [k for k in DEFAULT_SETTINGS if k.startswith("email_on_")]}.get(request.form.get("section"), [])
        for k in DEFAULT_SETTINGS:
            if k in checkboxes:
                set_setting(k, "1" if request.form.get(k) else "0")
            elif k in request.form:
                set_setting(k, request.form[k].strip())
        if request.files.get("logo_file") and request.files["logo_file"].filename:
            try:
                set_setting("logo_url", save_upload(request.files["logo_file"], "branding"))
            except ValueError as e:
                flash(str(e), "error")
        if request.form.get("section") == "website":
            db.session.commit(); flash("Website settings saved.", "success")
            return redirect(url_for("admin_website"))
        if request.form.get("section") in ("notify", "timetable"):
            db.session.commit(); flash("Saved.", "success")
            return redirect(request.referrer or url_for("admin_settings"))
        try:
            if sum(float(get_setting(k + "_max")) for k in ("ca1", "ca2", "exam")) != 100:
                flash("Warning: CA1 + CA2 + Exam maximums should add up to 100.", "error")
        except ValueError:
            flash("Score maximums must be numbers.", "error")
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin/settings.html", sessions=AcademicSession.query.order_by(AcademicSession.name.desc()).all(), db_live=USING_SUPABASE)


@app.route("/admin/sessions", methods=["POST"])
@admin
def admin_add_session():
    name = request.form["name"].strip()
    if name and not AcademicSession.query.filter_by(name=name).first():
        s = AcademicSession(name=name); db.session.add(s); db.session.flush()
        if request.form.get("make_current") or not get_setting("current_session_id"):
            set_setting("current_session_id", s.id)
        db.session.commit(); flash(f"Session {name} added.", "success")
    else:
        flash("Session name empty or already exists.", "error")
    return redirect(url_for("admin_settings"))


@app.route("/admin/classes", methods=["GET", "POST"])
@admin
def admin_classes():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            name = request.form["name"].strip()
            if name and not SchoolClass.query.filter_by(name=name).first():
                db.session.add(SchoolClass(name=name, level=request.form["level"],
                                           form_teacher_id=request.form.get("form_teacher_id") or None))
                flash("Class added.", "success")
            else:
                flash("Class name empty or exists.", "error")
        elif act == "update":
            c = db.get_or_404(SchoolClass, int(request.form["id"]))
            c.form_teacher_id = request.form.get("form_teacher_id") or None
            flash("Form teacher updated.", "success")
        elif act == "delete":
            c = db.get_or_404(SchoolClass, int(request.form["id"]))
            if Student.query.filter_by(class_id=c.id).count():
                flash("Move students out of this class before deleting it.", "error")
            else:
                ClassSubject.query.filter_by(class_id=c.id).delete()
                db.session.delete(c); flash("Class deleted.", "success")
        db.session.commit()
        return redirect(url_for("admin_classes"))
    classes = SchoolClass.query.order_by(SchoolClass.name).all()
    counts = dict(db.session.query(Student.class_id, func.count()).filter_by(status="active").group_by(Student.class_id).all())
    teachers = User.query.filter_by(role="teacher", active=True).order_by(User.full_name).all()
    return render_template("admin/classes.html", classes=classes, counts=counts, teachers=teachers,
                           levels=["JSS1", "JSS2", "JSS3", "SS1", "SS2", "SS3"])


@app.route("/admin/subjects", methods=["GET", "POST"])
@admin
def admin_subjects():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            name = request.form["name"].strip()
            if name and not Subject.query.filter(func.lower(Subject.name) == name.lower()).first():
                db.session.add(Subject(name=name, code=request.form.get("code", "").strip().upper()))
                flash("Subject added.", "success")
            else:
                flash("Subject empty or exists.", "error")
        elif act == "delete":
            s = db.get_or_404(Subject, int(request.form["id"]))
            if Score.query.filter_by(subject_id=s.id).first():
                flash("This subject already has scores; it cannot be deleted.", "error")
            else:
                ClassSubject.query.filter_by(subject_id=s.id).delete()
                db.session.delete(s); flash("Subject deleted.", "success")
        elif act == "assign":
            sid, tid = int(request.form["subject_id"]), request.form.get("teacher_id") or None
            class_ids = request.form.getlist("class_ids")
            for cid in class_ids:
                cs = ClassSubject.query.filter_by(class_id=int(cid), subject_id=sid).first()
                if not cs:
                    cs = ClassSubject(class_id=int(cid), subject_id=sid); db.session.add(cs)
                cs.teacher_id = tid
            flash(f"Assigned to {len(class_ids)} class(es).", "success")
        elif act == "unassign":
            cs = db.get_or_404(ClassSubject, int(request.form["id"]))
            db.session.delete(cs); flash("Removed.", "success")
        db.session.commit()
        return redirect(url_for("admin_subjects"))
    return render_template("admin/subjects.html", subjects=Subject.query.order_by(Subject.name).all(),
                           classes=SchoolClass.query.order_by(SchoolClass.name).all(),
                           teachers=User.query.filter_by(role="teacher", active=True).order_by(User.full_name).all(),
                           assigns=ClassSubject.query.join(SchoolClass).join(Subject).order_by(SchoolClass.name, Subject.name).all())


@app.route("/admin/teachers", methods=["GET", "POST"])
@admin
def admin_teachers():
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            un = request.form["username"].strip().lower()
            if User.query.filter(func.lower(User.username) == un).first():
                flash("Username taken.", "error")
            else:
                u = User(username=un, role="teacher", full_name=request.form["full_name"].strip(),
                         email=request.form.get("email"), phone=request.form.get("phone"))
                pw = request.form.get("password") or rand_code(6)
                u.set_password(pw); db.session.add(u)
                flash(f"Teacher created. Username: {un}  Password: {pw}", "success")
        elif act == "reset":
            u = db.get_or_404(User, int(request.form["id"]))
            pw = rand_code(6); u.set_password(pw)
            flash(f"New password for {u.username}: {pw}", "success")
        elif act == "toggle":
            u = db.get_or_404(User, int(request.form["id"]))
            u.active = not u.active; flash("Status updated.", "success")
        db.session.commit()
        return redirect(url_for("admin_teachers"))
    teachers = User.query.filter_by(role="teacher").order_by(User.full_name).all()
    load = {t.id: ClassSubject.query.filter_by(teacher_id=t.id).count() for t in teachers}
    return render_template("admin/teachers.html", teachers=teachers, load=load)


def create_student(f):
    reg = f["reg_no"].strip().upper()
    if Student.query.filter(func.lower(Student.reg_no) == reg.lower()).first() or User.query.filter(func.lower(User.username) == reg.lower()).first():
        raise ValueError(f"Reg no {reg} already exists")
    u = User(username=reg, role="student", full_name=f["full_name"].strip(), email=f.get("email") or None, phone=f.get("phone") or None)
    pw = (f.get("password") or "").strip() or reg.lower()
    u.set_password(pw); db.session.add(u); db.session.flush()
    gmail = (f.get("guardian_email") or "").strip().lower()
    st = Student(user_id=u.id, reg_no=reg, class_id=f.get("class_id") or None, gender=f.get("gender"), dob=f.get("dob"),
                 guardian_name=f.get("guardian_name"), guardian_phone=f.get("guardian_phone"), address=f.get("address"),
                 guardian_email=gmail if valid_email(gmail) else None)
    db.session.add(st)
    return st, pw


def ensure_parent(st):
    """Create (or reuse) the parent portal account for a student's guardian email. Returns (user, new_password or None)."""
    if not st.guardian_email:
        return None, None
    u = User.query.filter(func.lower(User.username) == st.guardian_email.lower()).first()
    pw = None
    if not u:
        u = User(username=st.guardian_email.lower(), role="parent", full_name=st.guardian_name or "Parent",
                 email=st.guardian_email.lower(), phone=st.guardian_phone)
        pw = rand_code(8, string.ascii_lowercase + string.digits)
        u.set_password(pw); db.session.add(u); db.session.flush()
    if u.role != "parent":
        return None, None
    st.parent_id = u.id
    return u, pw


def send_welcome(st, student_pw, parent_user=None, parent_pw=None):
    to = guardian_email(st)
    if to:
        send_email(to, f"Welcome to {get_setting('school_name')} – portal logins for {st.user.full_name}", "enrolled", "enrolment",
                   st=st, student_pw=student_pw, parent_user=parent_user, parent_pw=parent_pw,
                   button_text="Log in to the portal", button_url=abs_url("login"))


@app.route("/admin/students", methods=["GET", "POST"])
@admin
def admin_students():
    if request.method == "POST":
        try:
            st, pw = create_student(request.form)
            db.session.flush()
            pu, ppw = ensure_parent(st) if request.form.get("make_parent") else (None, None)
            db.session.commit()
            if request.form.get("make_parent"):
                send_welcome(st, pw, pu, ppw)
            flash(f"Student added. Login: {st.reg_no} / {pw}" + (f" · Parent login: {pu.username} / {ppw}" if ppw else "")
                  + (" · Emailed to the parent." if request.form.get("make_parent") and guardian_email(st) else ""), "success")
        except ValueError as e:
            db.session.rollback(); flash(str(e), "error")
        return redirect(url_for("admin_students", class_id=request.form.get("class_id", "")))
    q = Student.query.join(User, Student.user_id == User.id)
    cid, term_q, status = request.args.get("class_id"), request.args.get("q", "").strip(), request.args.get("status", "active")
    if cid:
        q = q.filter(Student.class_id == int(cid))
    if status:
        q = q.filter(Student.status == status)
    if term_q:
        q = q.filter((User.full_name.ilike(f"%{term_q}%")) | (Student.reg_no.ilike(f"%{term_q}%")))
    return render_template("admin/students.html", students=q.order_by(User.full_name).all(),
                           classes=SchoolClass.query.order_by(SchoolClass.name).all())


@app.route("/admin/students/import", methods=["POST"])
@admin
def admin_students_import():
    f = request.files.get("file")
    if not f:
        flash("Choose a CSV file.", "error"); return redirect(url_for("admin_students"))
    classes = {c.name.lower().replace(" ", ""): c.id for c in SchoolClass.query.all()}
    ok, errors = 0, []
    for i, row in enumerate(csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))), 2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("reg_no") or not row.get("full_name"):
            errors.append(f"row {i}: reg_no and full_name required"); continue
        row["class_id"] = classes.get(row.get("class", "").lower().replace(" ", ""))
        if row.get("class") and not row["class_id"]:
            errors.append(f"row {i}: class '{row['class']}' not found"); continue
        try:
            with db.session.begin_nested():
                create_student(row)
            ok += 1
        except ValueError as e:
            errors.append(f"row {i}: {e}")
    db.session.commit()
    flash(f"{ok} students imported (default password = reg no in lowercase)." + (" Problems: " + "; ".join(errors[:10]) if errors else ""),
          "error" if errors else "success")
    return redirect(url_for("admin_students"))


@app.route("/admin/students/template.csv")
@admin
def admin_students_template():
    return Response("reg_no,full_name,class,gender,dob,guardian_name,guardian_phone,guardian_email,address\n"
                    "GDS/2026/001,Adaeze Okafor,JSS 1A,F,2014-03-12,Mr. Okafor,08030000000,okafor@example.com,Lagos\n",
                    mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=students_template.csv"})


@app.route("/admin/students/<int:sid>", methods=["GET", "POST"])
@admin
def admin_student_edit(sid):
    st = db.get_or_404(Student, sid)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "reset":
            st.user.set_password(st.reg_no.lower()); db.session.commit()
            flash(f"Password reset to {st.reg_no.lower()}", "success")
            return redirect(request.url)
        if f.get("action") == "parent":
            if not st.guardian_email:
                flash("Add the parent's email first.", "error")
            else:
                pu, ppw = ensure_parent(st)
                if not pu:
                    flash("That email already belongs to a staff or student account.", "error")
                else:
                    if not ppw:  # existing parent: reset so we can send fresh details
                        ppw = rand_code(8, string.ascii_lowercase + string.digits); pu.set_password(ppw)
                    db.session.commit()
                    if valid_email(pu.email or ""):
                        send_email(pu.email, f"Your parent portal login – {get_setting('school_name')}", "account_password",
                                   "account", u=pu, pw=ppw, button_text="Log in", button_url=abs_url("login"))
                    flash(f"Parent portal ready. Login: {pu.username} / {ppw} (emailed to the parent).", "success")
            return redirect(request.url)
        st.user.full_name = f["full_name"].strip()
        st.user.email, st.user.phone = f.get("email") or None, f.get("phone") or None
        st.class_id = f.get("class_id") or None
        for k in ("gender", "dob", "guardian_name", "guardian_phone", "address", "status", "blood_group", "medical_note"):
            setattr(st, k, (f.get(k) or "").strip() or None)
        gmail = (f.get("guardian_email") or "").strip().lower()
        if gmail and not valid_email(gmail):
            flash("The parent email doesn't look right; it was not saved.", "error")
        else:
            st.guardian_email = gmail or None
        try:
            up = save_upload(request.files.get("photo"), "students")
            if up:
                st.photo_url = up
        except ValueError as e:
            flash(str(e), "error")
        st.user.active = st.status != "withdrawn"
        db.session.commit(); flash("Student updated.", "success")
        return redirect(url_for("admin_students", class_id=st.class_id or ""))
    sess, term = current_session(), current_term()
    items, total, paid, bal = fee_summary(st, sess, term) if sess else ([], 0, 0, 0)
    pays = Payment.query.filter_by(student_id=st.id).order_by(Payment.created_at.desc()).all()
    return render_template("admin/student_edit.html", st=st, classes=SchoolClass.query.order_by(SchoolClass.name).all(),
                           total=total, paid=paid, bal=bal, pays=pays)


@app.route("/admin/students/<int:sid>/report")
@admin
def admin_student_report(sid):
    st = db.get_or_404(Student, sid)
    sess = db.session.get(AcademicSession, int(request.args.get("session_id") or current_session().id))
    term = request.args.get("term") or current_term()
    return render_template("report_card.html", r=build_report(st, sess, term), admin_view=True)


@app.route("/admin/promote", methods=["GET", "POST"])
@admin
def admin_promote():
    classes = SchoolClass.query.order_by(SchoolClass.name).all()
    if request.method == "POST":
        frm, to = request.form["from_id"], request.form["to_id"]
        ids = [int(x) for x in request.form.getlist("student_ids")]
        n = 0
        for st in Student.query.filter(Student.id.in_(ids or [0]), Student.class_id == int(frm)):
            if to == "graduate":
                st.status = "graduated"
            else:
                st.class_id = int(to)
            n += 1
        db.session.commit(); flash(f"{n} students moved.", "success")
        return redirect(url_for("admin_promote", from_id=frm))
    frm = request.args.get("from_id")
    students, data = [], {}
    if frm:
        students = Student.query.filter_by(class_id=int(frm), status="active").join(User, Student.user_id == User.id).order_by(User.full_name).all()
        sess = current_session()
        if sess:
            data, _, _, _ = class_results(int(frm), sess.id, "Third Term")
    return render_template("admin/promote.html", classes=classes, students=students, frm=frm, data=data)


@app.route("/admin/fees", methods=["GET", "POST"])
@admin
def admin_fees():
    sess, term = current_session(), current_term()
    if request.method == "POST":
        act = request.form.get("action")
        if act == "add":
            try:
                amt = float(request.form["amount"])
                levels = request.form.getlist("levels") or ["ALL"]
                for lv in levels:
                    db.session.add(FeeItem(session_id=int(request.form["session_id"]), term=request.form["term"],
                                           level=lv, name=request.form["name"].strip(), amount=amt))
                flash("Fee item(s) added.", "success")
            except ValueError:
                flash("Amount must be a number.", "error")
        elif act == "delete":
            db.session.delete(db.get_or_404(FeeItem, int(request.form["id"]))); flash("Removed.", "success")
        elif act == "copy":
            src_term = request.form["src_term"]
            n = 0
            for it in FeeItem.query.filter_by(session_id=sess.id, term=src_term):
                db.session.add(FeeItem(session_id=sess.id, term=term, level=it.level, name=it.name, amount=it.amount)); n += 1
            flash(f"Copied {n} items from {src_term} to {term}.", "success")
        db.session.commit()
        return redirect(url_for("admin_fees"))
    items = FeeItem.query.filter_by(session_id=sess.id if sess else 0, term=term).order_by(FeeItem.level, FeeItem.name).all()
    totals = {}
    for lv in ["JSS1", "JSS2", "JSS3", "SS1", "SS2", "SS3"]:
        totals[lv] = sum(i.amount for i in items if i.level in ("ALL", lv))
    return render_template("admin/fees.html", items=items, totals=totals,
                           sessions=AcademicSession.query.order_by(AcademicSession.name.desc()).all(),
                           levels=["JSS1", "JSS2", "JSS3", "SS1", "SS2", "SS3"])


@app.route("/admin/payments", methods=["GET", "POST"])
@admin
def admin_payments():
    sess, term = current_session(), current_term()
    if request.method == "POST":  # record cash / bank payment
        st = Student.query.filter(func.lower(Student.reg_no) == request.form["reg_no"].strip().lower()).first()
        try:
            amt = float(request.form["amount"])
        except ValueError:
            amt = 0
        if not st or amt <= 0:
            flash("Valid reg no and amount required.", "error")
        else:
            p = Payment(student_id=st.id, session_id=sess.id, term=term, amount=amt, purpose="fees",
                        reference=f"MAN-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(4).upper()}",
                        channel=request.form.get("channel", "cash"), note=request.form.get("note"))
            db.session.add(p); db.session.flush(); finalize_payment(p)
            flash(f"Payment of {money(amt)} recorded for {st.user.full_name}.", "success")
        return redirect(url_for("admin_payments"))
    q = Payment.query.outerjoin(Student, Payment.student_id == Student.id).outerjoin(User, Student.user_id == User.id)
    status = request.args.get("status", "success")
    if status:
        q = q.filter(Payment.status == status)
    if request.args.get("q"):
        s = request.args["q"].strip()
        q = q.filter((Payment.reference.ilike(f"%{s}%")) | (Student.reg_no.ilike(f"%{s}%")) | (User.full_name.ilike(f"%{s}%")))
    pays = q.order_by(Payment.created_at.desc()).limit(500).all()
    if request.args.get("format") == "csv":
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Date", "Reference", "Reg No", "Name", "Purpose", "Term", "Amount", "Channel", "Status"])
        for p in pays:
            w.writerow([p.paid_at or p.created_at, p.reference, p.student.reg_no if p.student else (p.application.app_no if p.application else ""), p.student.user.full_name if p.student else (p.application.full_name if p.application else ""), p.purpose, p.term, p.amount, p.channel, p.status])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=payments.csv"})
    # debtors list
    debtors = []
    if sess:
        for st in Student.query.filter_by(status="active").join(User, Student.user_id == User.id).order_by(User.full_name):
            _, t, pd, b = fee_summary(st, sess, term)
            if b > 0:
                debtors.append((st, t, pd, b))
    return render_template("admin/payments.html", pays=pays, debtors=debtors,
                           sessions={s.id: s.name for s in AcademicSession.query.all()})


@app.route("/admin/pins", methods=["GET", "POST"])
@admin
def admin_pins():
    sess, term = current_session(), current_term()
    if request.method == "POST":
        try:
            n = max(1, min(int(request.form["count"]), 2000))
        except ValueError:
            n = 0
        batch = f"B{datetime.utcnow():%Y%m%d%H%M%S}"
        bind = request.form.get("bind_term")
        for _ in range(n):
            new_card(sess.id if bind else None, term if bind else None, batch)
        db.session.commit()
        flash(f"{n} PINs generated in batch {batch}.", "success")
        return redirect(url_for("admin_pins", batch=batch))
    q = ScratchCard.query
    if request.args.get("batch"):
        q = q.filter_by(batch=request.args["batch"])
    if request.args.get("state") == "unused":
        q = q.filter_by(used_count=0)
    elif request.args.get("state") == "used":
        q = q.filter(ScratchCard.used_count > 0)
    cards = q.order_by(ScratchCard.created_at.desc()).limit(1000).all()
    fmt = request.args.get("format")
    if fmt == "csv":
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Serial", "PIN", "Batch", "Max Uses", "Used", "Student"])
        for c in cards:
            w.writerow([c.serial, c.pin, c.batch, c.max_uses, c.used_count, c.student.reg_no if c.student else ""])
        return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=pins.csv"})
    if fmt == "print":
        return render_template("admin/pins_print.html", cards=[c for c in cards if c.used_count == 0])
    batches = [b[0] for b in db.session.query(ScratchCard.batch).distinct().order_by(ScratchCard.batch.desc()).all()]
    return render_template("admin/pins.html", cards=cards, batches=batches,
                           sessions={s.id: s.name for s in AcademicSession.query.all()})


@app.route("/admin/results", methods=["GET", "POST"])
@admin
def admin_results():
    sess, term = current_session(), current_term()
    if request.method == "POST":
        cid = int(request.form["class_id"])
        pub = Publication.query.filter_by(class_id=cid, session_id=sess.id, term=term).first()
        if request.form["action"] == "publish" and not pub:
            db.session.add(Publication(class_id=cid, session_id=sess.id, term=term)); db.session.commit()
            n = 0
            if request.form.get("notify") and setting_on("email_on_results"):
                for st in Student.query.filter_by(class_id=cid, status="active"):
                    to = guardian_email(st)
                    if to and send_email(to, f"{term} results are out – {st.user.full_name}", "results_published", "results",
                                         st=st, term=term, session_name=sess.name,
                                         button_text="Check the result", button_url=abs_url("public_result")):
                        n += 1
            flash("Results published." + (f" {n} parent(s) emailed." if n else ""), "success")
        elif request.form["action"] == "unpublish" and pub:
            db.session.delete(pub); flash("Results unpublished — teachers can edit again.", "success")
        db.session.commit()
        return redirect(url_for("admin_results"))
    rows = []
    for cl in SchoolClass.query.order_by(SchoolClass.name).all():
        n_st = Student.query.filter_by(class_id=cl.id, status="active").count()
        cs_list = ClassSubject.query.filter_by(class_id=cl.id).all()
        expected = n_st * len(cs_list)
        entered = Score.query.filter_by(class_id=cl.id, session_id=sess.id if sess else 0, term=term).count()
        rows.append({"cl": cl, "students": n_st, "subjects": len(cs_list), "expected": expected, "entered": entered,
                     "published": is_published(cl.id, sess.id, term) if sess else False, "cs_list": cs_list})
    return render_template("admin/results.html", rows=rows)


@app.route("/admin/announcements", methods=["GET", "POST"])
@admin
def admin_announcements():
    if request.method == "POST":
        if request.form.get("action") == "delete":
            db.session.delete(db.get_or_404(Announcement, int(request.form["id"])))
        else:
            a = Announcement(title=request.form["title"].strip(), body=request.form["body"].strip(),
                             audience=request.form.get("audience", "all"))
            db.session.add(a); db.session.commit()
            n = 0
            if request.form.get("email_parents"):
                sent = set()
                for st in Student.query.filter_by(status="active"):
                    to = guardian_email(st)
                    if to and to.lower() not in sent:
                        sent.add(to.lower())
                        send_email(to, a.title, "broadcast", "broadcast", title=a.title, body=a.body,
                                   button_text="Open the parent portal", button_url=abs_url("login")); n += 1
            flash("Announcement posted." + (f" Emailed to {n} parent(s)." if n else ""), "success")
            return redirect(url_for("admin_announcements"))
        db.session.commit(); flash("Done.", "success")
        return redirect(url_for("admin_announcements"))
    return render_template("admin/announcements.html", items=Announcement.query.order_by(Announcement.created_at.desc()).all())


@app.route("/admin/admins", methods=["POST"])
@admin
def admin_add_admin():
    un = request.form["username"].strip().lower()
    if User.query.filter(func.lower(User.username) == un).first():
        flash("Username taken.", "error")
    else:
        u = User(username=un, role="admin", full_name=request.form["full_name"].strip())
        u.set_password(request.form["password"]); db.session.add(u); db.session.commit()
        flash("Admin account created.", "success")
    return redirect(url_for("admin_settings"))


@app.route("/admin/emails")
@admin
def admin_emails():
    q = EmailLog.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("q"):
        q = q.filter(EmailLog.to.ilike(f"%{request.args['q'].strip()}%") | EmailLog.subject.ilike(f"%{request.args['q'].strip()}%"))
    counts = dict(db.session.query(EmailLog.status, func.count()).group_by(EmailLog.status).all())
    return render_template("admin/emails.html", items=q.order_by(EmailLog.created_at.desc()).limit(300).all(), counts=counts)


@app.route("/admin/emails/<int:eid>")
@admin
def admin_email_view(eid):
    return db.get_or_404(EmailLog, eid).body


@app.route("/admin/emails/<int:eid>/resend", methods=["POST"])
@admin
def admin_email_resend(eid):
    e = db.get_or_404(EmailLog, eid)
    if not mail_ready():
        flash("Set up the email server first (SMTP settings).", "error")
    else:
        e.status, e.error = "queued", ""; db.session.commit()
        text = re.sub(r"<[^>]+>", "", e.body)
        threading.Thread(target=_deliver, args=(e.id, e.to, e.subject, e.body, text), daemon=True).start()
        flash(f"Resending to {e.to}.", "success")
    return redirect(url_for("admin_emails"))


@app.route("/admin/test-email", methods=["POST"])
@admin
def admin_test_email():
    to = request.form.get("to", "").strip()
    e = send_email(to, f"Test email from {get_setting('school_name')}", "test", "test")
    if not e:
        flash("Enter a valid email address.", "error")
    elif e.status == "not_configured":
        flash("Saved to the email log, but not sent: the email server isn't set up yet (SMTP settings).", "error")
    else:
        flash(f"Test email sent to {to}. Check the inbox (and spam folder).", "success")
    return redirect(url_for("admin_settings") + "#email")


@app.route("/admin/fee-reminders", methods=["POST"])
@admin
def admin_fee_reminders():
    sess, term = current_session(), current_term()
    n = 0
    for st in Student.query.filter_by(status="active"):
        _, total, paid, bal = fee_summary(st, sess, term)
        to = guardian_email(st)
        if bal > 0 and to:
            send_email(to, f"School fees reminder – {st.user.full_name} ({term})", "fee_reminder", "reminder",
                       st=st, total=total, paid=paid, balance=bal, term=term, session_name=sess.name,
                       button_text="Pay fees online", button_url=abs_url("login")); n += 1
    flash(f"Fee reminders emailed to {n} parent(s)." if n else "No owing students with a parent email.", "success" if n else "error")
    return redirect(url_for("admin_payments"))


# ─────────────────────────── ADMIN: WEBSITE CMS ───────────────────────────
@app.route("/admin/website", methods=["GET"])
@admin
def admin_website():
    return render_template("admin/website.html")


@app.route("/admin/posts", methods=["GET", "POST"])
@admin
def admin_posts():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete":
            db.session.delete(db.get_or_404(Post, int(f["id"]))); db.session.commit()
            flash("Post deleted.", "success"); return redirect(url_for("admin_posts"))
        if f.get("action") == "toggle":
            p = db.get_or_404(Post, int(f["id"])); p.published = not p.published; db.session.commit()
            return redirect(url_for("admin_posts"))
        p = db.session.get(Post, int(f["id"])) if f.get("id") else None
        if not f.get("title", "").strip():
            flash("A title is required.", "error"); return redirect(url_for("admin_posts"))
        if not p:
            p = Post(slug=slugify(f["title"])); db.session.add(p)
        p.kind = f.get("kind", "news")
        p.title, p.summary, p.body = f["title"].strip(), f.get("summary", "").strip()[:300], f.get("body", "")
        p.event_time, p.venue = f.get("event_time", ""), f.get("venue", "")
        try:
            p.event_date = datetime.strptime(f["event_date"], "%Y-%m-%d").date() if f.get("event_date") else None
        except ValueError:
            p.event_date = None
        if p.kind == "event" and not p.event_date:
            flash("Events need a date.", "error"); db.session.rollback(); return redirect(url_for("admin_posts", edit=f.get("id", "")))
        try:
            up = save_upload(request.files.get("image"), "posts")
            p.image_url = up or f.get("image_url", "").strip()
        except ValueError as e:
            flash(str(e), "error")
        db.session.commit(); flash("Post saved.", "success")
        return redirect(url_for("admin_posts"))
    edit = db.session.get(Post, int(request.args["edit"])) if request.args.get("edit") else None
    return render_template("admin/posts.html", posts=Post.query.order_by(Post.created_at.desc()).all(), edit=edit)


@app.route("/admin/gallery", methods=["GET", "POST"])
@admin
def admin_gallery():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete":
            db.session.delete(db.get_or_404(GalleryItem, int(f["id"])))
            db.session.commit(); flash("Photo removed.", "success")
            return redirect(url_for("admin_gallery"))
        n = 0
        try:
            for fs in request.files.getlist("images"):
                if fs and fs.filename:
                    db.session.add(GalleryItem(image_url=save_upload(fs, "gallery"), caption=f.get("caption", "")[:160],
                                               category=f.get("category", "School life").strip() or "School life")); n += 1
            if f.get("image_url", "").strip():
                db.session.add(GalleryItem(image_url=f["image_url"].strip(), caption=f.get("caption", "")[:160],
                                           category=f.get("category", "School life").strip() or "School life")); n += 1
        except ValueError as e:
            flash(str(e), "error")
        db.session.commit(); flash(f"{n} photo(s) added.", "success" if n else "error")
        return redirect(url_for("admin_gallery"))
    return render_template("admin/gallery.html", items=GalleryItem.query.order_by(GalleryItem.created_at.desc()).all())


@app.route("/admin/testimonials", methods=["GET", "POST"])
@admin
def admin_testimonials():
    if request.method == "POST":
        f = request.form
        if f.get("action") == "delete":
            db.session.delete(db.get_or_404(Testimonial, int(f["id"])))
        elif f.get("action") == "toggle":
            t = db.get_or_404(Testimonial, int(f["id"])); t.show = not t.show
        elif f.get("name", "").strip() and f.get("text", "").strip():
            db.session.add(Testimonial(name=f["name"].strip(), role=f.get("role", "Parent").strip(), text=f["text"].strip()))
        db.session.commit(); flash("Saved.", "success")
        return redirect(url_for("admin_testimonials"))
    return render_template("admin/testimonials.html", items=Testimonial.query.order_by(Testimonial.id.desc()).all())


APP_STATUSES = ["submitted", "reviewing", "invited", "admitted", "declined"]


@app.route("/admin/admissions")
@admin
def admin_admissions():
    st = request.args.get("status", "")
    q = Application.query
    if st:
        q = q.filter_by(status=st)
    counts = dict(db.session.query(Application.status, func.count()).group_by(Application.status).all())
    return render_template("admin/admissions.html", apps=q.order_by(Application.created_at.desc()).all(),
                           counts=counts, statuses=APP_STATUSES)


@app.route("/admin/admissions/<int:aid>", methods=["GET", "POST"])
@admin
def admin_admission(aid):
    a = db.get_or_404(Application, aid)
    if request.method == "POST":
        f = request.form
        if f.get("action") == "enroll":
            if a.student_id:
                flash("Already enrolled.", "error")
            else:
                try:
                    st, pw = create_student({"reg_no": f["reg_no"], "full_name": a.full_name, "class_id": f.get("class_id") or None,
                                             "gender": a.gender, "dob": a.dob, "guardian_name": a.parent_name,
                                             "guardian_phone": a.phone, "guardian_email": a.email, "address": a.address})
                    db.session.flush()
                    if a.photo_url:
                        st.photo_url = a.photo_url
                    pu, ppw = ensure_parent(st)
                    was_admitted = a.status == "admitted"
                    a.student_id, a.status = st.id, "admitted"
                    db.session.commit()
                    send_welcome(st, pw, pu, ppw)
                    if not was_admitted and setting_on("email_on_status"):
                        send_email(a.email, f"Offer of admission – {a.full_name}", "application_status", "admission", a=a,
                                   button_text="View admission letter", button_url=abs_url("admission_letter", token=a.token))
                    flash(f"Enrolled. Student login: {st.reg_no} / {pw}" + (f" · Parent login: {pu.username} / {ppw}" if ppw else "")
                          + (" · Logins emailed to the parent." if guardian_email(st) else ""), "success")
                except ValueError as e:
                    db.session.rollback(); flash(str(e), "error")
        else:
            old = (a.status, a.exam_date or "", a.admin_note or "")
            if f.get("status") in APP_STATUSES:
                a.status = f["status"]
            a.exam_date, a.admin_note = f.get("exam_date", "")[:64], f.get("admin_note", "")
            db.session.commit()
            changed = old != (a.status, a.exam_date or "", a.admin_note or "")
            msg = "Application updated."
            if changed and f.get("notify") and setting_on("email_on_status") and a.status != "submitted":
                subj = {"invited": "Entrance examination invitation", "admitted": "Offer of admission",
                        "declined": "Update on your application"}.get(a.status, "Your application is being reviewed")
                btn = (abs_url("admission_letter", token=a.token), "View admission letter") if a.status == "admitted" \
                    else (abs_url("admission_status", t=a.token), "Track your application")
                if send_email(a.email, f"{subj} – {a.full_name}", "application_status", "admission", a=a,
                              button_url=btn[0], button_text=btn[1]):
                    msg += f" Email sent to {a.email}."
            flash(msg, "success")
        return redirect(request.url)
    return render_template("admin/admission_view.html", a=a, statuses=APP_STATUSES,
                           classes=SchoolClass.query.order_by(SchoolClass.name).all())


@app.route("/admin/messages", methods=["GET", "POST"])
@admin
def admin_messages():
    if request.method == "POST":
        m = db.get_or_404(ContactMessage, int(request.form["id"]))
        if request.form.get("action") == "delete":
            db.session.delete(m)
        else:
            m.read = not m.read
        db.session.commit()
        return redirect(url_for("admin_messages"))
    return render_template("admin/messages.html", items=ContactMessage.query.order_by(ContactMessage.created_at.desc()).all())


# ─────────────────────────── ERRORS / INIT ───────────────────────────
@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="You don't have access to this page."), 403


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="Page not found."), 404


@app.errorhandler(400)
def e400(e):
    return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request")), 400


def migrate_columns():
    """Add any new model columns to tables that already exist (safe to run on every start)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    prep = db.engine.dialect.identifier_preparer
    existing_tables = set(insp.get_table_names())
    with db.engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ctype = col.type.compile(dialect=db.engine.dialect)
                conn.execute(text(f"ALTER TABLE {prep.quote(table.name)} ADD COLUMN {prep.quote(col.name)} {ctype}"))
                log.info("Added column %s.%s", table.name, col.name)
        if db.engine.dialect.name == "postgresql":
            if "payment" in existing_tables:
                conn.execute(text('ALTER TABLE payment ALTER COLUMN student_id DROP NOT NULL'))
            # Supabase exposes the public schema through its REST API. Turning on Row Level Security with no
            # policies blocks that API completely, while this app (the table owner) keeps full access.
            for table in db.metadata.sorted_tables:
                conn.execute(text(f"ALTER TABLE {prep.quote(table.name)} ENABLE ROW LEVEL SECURITY"))


def init_db():
    db.create_all()
    migrate_columns()
    for a in Application.query.filter(Application.token.is_(None)).all():
        a.token = secrets.token_urlsafe(16)
    if not User.query.filter_by(role="admin").first():
        u = User(username="admin", role="admin", full_name="School Administrator")
        u.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
        db.session.add(u)
    if not AcademicSession.query.first():
        y = datetime.now().year
        s = AcademicSession(name=f"{y}/{y + 1}" if datetime.now().month >= 9 else f"{y - 1}/{y}")
        db.session.add(s); db.session.flush()
        set_setting("current_session_id", s.id)
    if not db.session.get(Setting, "site_seeded"):
        seed_site_content()
        set_setting("site_seeded", "1")
    if not db.session.get(Setting, "photos_v2"):  # school photos replace the starter illustrations
        GalleryItem.query.filter(GalleryItem.image_url.like("/static/img/%")).delete(synchronize_session=False)
        from datetime import timedelta
        base = datetime.utcnow()
        photos = [  # last in list = newest = shown first
            ("uniform", "Our school uniform", "School life"), ("gate", "The school entrance", "Campus"),
            ("library", "Reading in the library", "Academics"), ("lab", "Microscope practical in the science lab", "Academics"),
            ("ict", "Computer lesson in the ICT lab", "Academics"), ("classwork", "Class work in session", "Academics"),
            ("classroom", "A lesson in progress", "Academics"), ("students", "Students in the school courtyard", "School life"),
            ("courtyard", "Our classroom blocks and courtyard", "Campus"),
        ]
        for i, (img, cap, cat) in enumerate(photos):
            db.session.add(GalleryItem(image_url=f"/static/img/photos/{img}.jpg", caption=cap, category=cat,
                                       created_at=base + timedelta(seconds=i)))
        set_setting("photos_v2", "1")
    db.session.commit()


def seed_site_content():
    """Starter website content so the public site is not empty. Edit or delete from Admin → Website."""
    from datetime import timedelta
    t = date.today()
    events = [
        ("Inter-house sports competition", t + timedelta(days=21), "9:00am", "School sports field",
         "Four houses, one trophy. Parents are welcome to cheer from the stands."),
        ("Open day and school tour", t + timedelta(days=35), "10:00am", "Main hall",
         "Prospective parents can tour the classrooms and labs, meet teachers and ask about admission."),
        ("Entrance examination", t + timedelta(days=49), "8:30am", "Examination hall",
         "For JSS 1 and SS 1 applicants. Bring a pencil, eraser, ruler and your application number."),
    ]
    for title, d, tm, venue, summ in events:
        db.session.add(Post(kind="event", title=title, slug=slugify(title), summary=summ, body=summ, event_date=d,
                            event_time=tm, venue=venue, image_url=""))
    news = [
        ("Students shine at state science quiz", "Our SS 2 team reached the final round of the state science quiz.",
         "Our SS 2 science team represented the school at the state science quiz and reached the final round. We are proud of their preparation and teamwork, and thank their teachers for the extra hours."),
        ("New ICT laboratory opened", "Every student now has weekly practical computer lessons.",
         "The new ICT laboratory gives every class weekly hands-on lessons in typing, office tools, internet safety and basic programming."),
        ("Resumption and fees notice", "School fees can now be paid online through the student portal.",
         "Parents can now pay school fees online by logging in to the student portal with the child's registration number. Receipts are issued immediately."),
    ]
    for title, summ, body in news:
        db.session.add(Post(kind="news", title=title, slug=slugify(title), summary=summ, body=body))
    for name, role, text in [
        ("Mrs. Folake A.", "Parent of a JSS 3 student", "My son's teachers know him by name and call me when something needs attention. His handwriting, confidence and grades have all improved."),
        ("Mr. Chinedu O.", "Parent", "Paying fees online and checking results on the portal saves me a trip to the school every term. The school is organised."),
        ("Esther I.", "SS 3 student", "The science lab and the extra WAEC classes made a real difference for me. The teachers push us but they also care."),
    ]:
        db.session.add(Testimonial(name=name, role=role, text=text))


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 5000)))
