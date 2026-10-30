"""Fill the database with demo data so you can try every feature.
Run:  python seed_demo.py      (only works on an empty database)
"""
import random
from datetime import date, timedelta
from app import (app, db, User, SchoolClass, Subject, ClassSubject, Student, Score, Remark, FeeItem, Attendance,
                 Homework, TimetableSlot, Publication, Announcement, current_session, current_term, new_card,
                 create_student, ensure_parent, periods)

FIRST = ["Adaeze", "Tunde", "Chioma", "Ibrahim", "Fatima", "Emeka", "Bola", "Ngozi", "Yusuf", "Kemi", "Segun", "Amaka", "Musa", "Funmi", "Obinna", "Zainab"]
LAST = ["Okafor", "Adeyemi", "Bello", "Eze", "Ogunleye", "Abubakar", "Nwosu", "Balogun", "Okeke", "Lawal"]

with app.app_context():
    if SchoolClass.query.first():
        print("Data already exists — skipping."); raise SystemExit
    random.seed(7)
    sess, term = current_session(), current_term()
    teachers = []
    for i, name in enumerate(["Mr. Adebayo Kola", "Mrs. Grace Uche", "Mr. Sani Ahmed", "Miss Joy Etim"]):
        u = User(username=f"teacher{i+1}", role="teacher", full_name=name); u.set_password("teacher123")
        db.session.add(u); teachers.append(u)
    db.session.flush()
    classes = []
    for i, (n, lv) in enumerate([("JSS 1A", "JSS1"), ("JSS 2A", "JSS2"), ("SS 1A", "SS1")]):
        c = SchoolClass(name=n, level=lv, form_teacher_id=teachers[i].id); db.session.add(c); classes.append(c)
    subjects = []
    for n, c in [("Mathematics", "MTH"), ("English Language", "ENG"), ("Basic Science", "BSC"), ("Civic Education", "CVE"), ("Computer Studies", "CMP"), ("Agricultural Science", "AGR")]:
        s = Subject(name=n, code=c); db.session.add(s); subjects.append(s)
    db.session.flush()
    links = {}
    for c in classes:
        for j, s in enumerate(subjects):
            cs = ClassSubject(class_id=c.id, subject_id=s.id, teacher_id=teachers[j % len(teachers)].id)
            db.session.add(cs); links[(c.id, s.id)] = cs
    db.session.flush()

    # school days so far this term (last 3 weeks, weekdays)
    days = [date.today() - timedelta(days=i) for i in range(1, 22)]
    days = [d for d in days if d.weekday() < 5]

    k = 1
    for c in classes:
        for _ in range(8):
            last = random.choice(LAST)
            st, _ = create_student({"reg_no": f"GDS/2026/{k:03d}", "full_name": f"{random.choice(FIRST)} {last}",
                                    "class_id": c.id, "gender": random.choice("MF"), "guardian_name": f"Mr/Mrs {last}",
                                    "guardian_phone": f"080{random.randint(10000000, 99999999)}",
                                    "guardian_email": f"parent{k:03d}@example.com"})
            db.session.flush(); k += 1
            for s in subjects:
                db.session.add(Score(student_id=st.id, subject_id=s.id, class_id=c.id, session_id=sess.id, term=term,
                                     ca1=random.randint(8, 20), ca2=random.randint(8, 20), exam=random.randint(20, 60)))
            db.session.add(Remark(student_id=st.id, session_id=sess.id, term=term,
                                  teacher_comment="A good result, keep working hard.", principal_comment="Promising performance.",
                                  punctuality="4", neatness="5", conduct="4"))
            for d in days:
                db.session.add(Attendance(student_id=st.id, class_id=c.id, session_id=sess.id, term=term, day=d,
                                          status=random.choices("PLA", weights=[88, 7, 5])[0]))

    # one parent with two children (parent001@example.com), so the parent portal has something to show
    first = Student.query.filter_by(reg_no="GDS/2026/001").first()
    second = Student.query.filter_by(reg_no="GDS/2026/010").first()
    second.guardian_email = first.guardian_email
    parent, _ = ensure_parent(first); parent.set_password("parent123"); parent.full_name = "Mrs. Okafor"
    ensure_parent(second)

    # timetable for every class
    plist = periods()
    for c in classes:
        for d in range(5):
            for pi in range(len(plist)):
                if pi == 3:
                    db.session.add(TimetableSlot(class_id=c.id, day=d, period=pi, label="Break"))
                else:
                    db.session.add(TimetableSlot(class_id=c.id, day=d, period=pi, subject_id=subjects[(d + pi) % len(subjects)].id))

    # homework
    for c in classes:
        db.session.add(Homework(class_subject_id=links[(c.id, subjects[0].id)].id, title="Exercise 4.2, questions 1–10",
                                details="Show all working. Submit in your Mathematics notebook.", due_date=date.today() + timedelta(days=2),
                                teacher_id=teachers[0].id))
        db.session.add(Homework(class_subject_id=links[(c.id, subjects[1].id)].id, title="Composition: My best day at school",
                                details="Not less than 250 words.", due_date=date.today() + timedelta(days=5), teacher_id=teachers[1].id))

    for name, amt, lv in [("Tuition", 85000, "ALL"), ("Development Levy", 10000, "ALL"), ("Books & Stationery", 15000, "JSS1"), ("Lab Fee", 7500, "SS1")]:
        db.session.add(FeeItem(session_id=sess.id, term=term, level=lv, name=name, amount=amt))
    db.session.add(Publication(class_id=classes[0].id, session_id=sess.id, term=term))
    for _ in range(10):
        new_card(None, None, "DEMO")
    db.session.add(Announcement(title="Welcome to the new term", body="Resumption is on Monday. School fees can now be paid online through the portal."))
    db.session.commit()
    print("Demo data created.")
    print("  admin    : admin / admin123")
    print("  teacher  : teacher1 / teacher123")
    print("  student  : GDS/2026/001 / gds/2026/001")
    print("  parent   : parent001@example.com / parent123  (two children)")
    print("  JSS 1A results are published. Unused PINs are on Admin → Result PINs.")
