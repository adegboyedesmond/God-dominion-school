"""
God Dominion School — setup checker.

Run:   python check_setup.py you@email.com
It tests every connection in your .env and opens every page, then prints PASS / FAIL with what to fix.
Safe to run on the live database: it only reads, plus one tiny test file in Storage (deleted again),
one test email to the address you give, and one ₦100 Flutterwave TEST checkout link (nothing is charged).
"""
import os, sys, re, uuid, smtplib
from dotenv import load_dotenv

load_dotenv()
G, R, Y, B, X = "\033[92m", "\033[91m", "\033[93m", "\033[1m", "\033[0m"
if os.name == "nt":
    os.system("")  # turn on colours in Windows terminal
results = []


def ok(name, msg=""):
    results.append((True, name)); print(f"{G}  PASS{X}  {name}  {msg}")


def bad(name, msg, fix=""):
    results.append((False, name)); print(f"{R}  FAIL{X}  {name}  {msg}" + (f"\n        {Y}→ {fix}{X}" if fix else ""))


def warn(name, msg):
    print(f"{Y}  SKIP{X}  {name}  {msg}")


to_email = sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMTP_USER", "")
print(f"\n{B}God Dominion School — checking your setup{X}\n")

# ── 1. .env values ──────────────────────────────────────────
print(f"{B}1. Settings in .env{X}")
need = ["SECRET_KEY", "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "FLW_SECRET_KEY", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"]
for k in need:
    v = os.getenv(k, "").strip()
    if not v:
        bad(k, "is empty", f"Fill in {k} in your .env file")
    elif "[YOUR-PASSWORD]" in v or "xxxx" in v or v == "put-a-long-random-string-here":
        bad(k, "still has the example text", f"Replace the example value of {k} with your real one")
    else:
        ok(k)
if os.getenv("ADMIN_PASSWORD", "admin123") == "admin123":
    print(f"{Y}  NOTE{X}  ADMIN_PASSWORD is still admin123 — change it before going live")

# ── 2. Database ─────────────────────────────────────────────
print(f"\n{B}2. Supabase database{X}")
try:
    import app as A
    from sqlalchemy import inspect, text
    with A.app.app_context():
        if not A.USING_SUPABASE:
            bad("Database", "using the local test database", "DATABASE_URL is empty")
        else:
            A.db.session.execute(text("select 1"))
            tables = set(inspect(A.db.engine).get_table_names())
            missing = [t.name for t in A.db.metadata.sorted_tables if t.name not in tables]
            if missing:
                bad("Tables", f"missing: {', '.join(missing)}", "Start python app.py once so it creates them")
            else:
                ok("Connected", f"({len(tables)} tables)")
            counts = {m.__tablename__: m.query.count() for m in (A.Student, A.Application, A.Payment, A.User)}
            ok("Data", ", ".join(f"{k}: {v}" for k, v in counts.items()))
except Exception as e:
    msg = str(e).splitlines()[0][:200]
    fix = "Check DATABASE_URL: use the Transaction pooler link, real password, no [ ] brackets"
    if "password authentication failed" in msg:
        fix = "Wrong database password in DATABASE_URL (reset it in Supabase → Database settings)"
    elif "could not translate host" in msg or "Name or service not known" in msg or "getaddrinfo" in msg:
        fix = "Can't find the server — check the host part of DATABASE_URL and your internet"
    bad("Database", msg, fix)
    A = None

# ── 3. Storage ──────────────────────────────────────────────
print(f"\n{B}3. Supabase Storage (photos & files){X}")
import requests
url, key, bucket = os.getenv("SUPABASE_URL", "").rstrip("/"), os.getenv("SUPABASE_SERVICE_KEY", "").strip(), os.getenv("SUPABASE_BUCKET", "school-media")
if url and key:
    try:
        h = {"apikey": key, **({"Authorization": f"Bearer {key}"} if key.startswith("eyJ") else {})}
        name = f"setup-check/{uuid.uuid4().hex[:8]}.txt"
        r = requests.post(f"{url}/storage/v1/object/{bucket}/{name}", data=b"ok", timeout=30,
                          headers={**h, "Content-Type": "text/plain", "x-upsert": "true"})
        if r.status_code >= 300:
            fix = f"Create a PUBLIC bucket named '{bucket}' in Supabase → Storage" if "not found" in r.text.lower() \
                else "Check SUPABASE_SERVICE_KEY is the SECRET key (sb_secret_...), not the publishable one"
            bad("Upload", f"HTTP {r.status_code}: {r.text[:150]}", fix)
        else:
            pub = requests.get(f"{url}/storage/v1/object/public/{bucket}/{name}", timeout=30)
            if pub.status_code == 200:
                ok("Upload + public link")
            else:
                bad("Public link", f"HTTP {pub.status_code}", f"Make the '{bucket}' bucket PUBLIC (Storage → bucket → Edit)")
            requests.delete(f"{url}/storage/v1/object/{bucket}", json={"prefixes": [name]}, headers=h, timeout=30)
    except Exception as e:
        bad("Storage", str(e)[:200], "Check SUPABASE_URL and your internet")
else:
    warn("Storage", "SUPABASE_URL / SUPABASE_SERVICE_KEY not set — photos will be saved on the server instead")

# ── 4. Email ────────────────────────────────────────────────
print(f"\n{B}4. Email (SMTP){X}")
brevo = os.getenv("BREVO_API_KEY", "").strip()
if brevo:
    try:
        r = requests.get("https://api.brevo.com/v3/account", headers={"api-key": brevo, "accept": "application/json"}, timeout=30)
        ok("Brevo key", f"({r.json().get('email', '')})") if r.status_code == 200 else bad("Brevo key", f"HTTP {r.status_code}", "Check BREVO_API_KEY (Brevo → SMTP & API → API keys)")
    except Exception as e:
        bad("Brevo", str(e)[:200])
host, port = os.getenv("SMTP_HOST", ""), int(os.getenv("SMTP_PORT", "587") or 587)
user, pw = os.getenv("SMTP_USER", ""), os.getenv("SMTP_PASSWORD", "").replace(" ", "")
if not brevo and host and user and pw:
    try:
        s = smtplib.SMTP_SSL(host, port, timeout=30) if (os.getenv("SMTP_SSL") == "1" or port == 465) else smtplib.SMTP(host, port, timeout=30)
        if not isinstance(s, smtplib.SMTP_SSL):
            s.starttls()
        s.login(user, pw); s.quit()
        ok("Login", f"({user})")
        if A and re.match(r"[^@\s]+@[^@\s]+\.\w+", to_email or ""):
            with A.app.test_request_context():
                A.app.config["TESTING"] = False
                e = A.send_email(to_email, "Setup check – God Dominion School", "test", "test")
            import time
            for _ in range(30):
                time.sleep(0.5)
                with A.app.app_context():
                    st = A.db.session.get(A.EmailLog, e.id)
                    if st.status != "queued":
                        break
            if st.status == "sent":
                ok("Test email sent", f"to {to_email} — check inbox and Spam")
            else:
                bad("Test email", f"{st.status}: {st.error[:150]}")
    except smtplib.SMTPAuthenticationError:
        bad("Login", "Gmail rejected the username/password",
            "Use the 16-letter App Password (no spaces) in SMTP_PASSWORD, and the same Gmail in SMTP_USER")
    except Exception as e:
        bad("Email", str(e)[:200], "Check SMTP_HOST / SMTP_PORT (Gmail: smtp.gmail.com, 587)")
elif not brevo:
    warn("Email", "SMTP settings not filled in")

# ── 5. Flutterwave ──────────────────────────────────────────
print(f"\n{B}5. Flutterwave{X}")
fk = os.getenv("FLW_SECRET_KEY", "").strip()
if fk:
    if not fk.startswith("FLWSECK"):
        bad("Key format", "doesn't start with FLWSECK", "Copy the SECRET key from Flutterwave → Settings → API Keys")
    elif "_TEST" not in fk:
        print(f"{Y}  NOTE{X}  This is a LIVE key — skipping the checkout test so no real link is created")
        ok("Key format", "(live)")
    else:
        try:
            r = requests.post("https://api.flutterwave.com/v3/payments", timeout=30,
                              headers={"Authorization": f"Bearer {fk}", "Content-Type": "application/json"},
                              json={"tx_ref": f"CHECK-{uuid.uuid4().hex[:10]}", "amount": 100, "currency": "NGN",
                                    "redirect_url": "https://example.com", "customer": {"email": to_email or "test@example.com", "name": "Setup check"},
                                    "customizations": {"title": "God Dominion School"}}).json()
            if r.get("status") == "success":
                ok("Test checkout link created", r["data"]["link"])
            else:
                bad("Flutterwave", str(r.get("message"))[:200], "Check FLW_SECRET_KEY (test key, Settings → API Keys)")
        except Exception as e:
            bad("Flutterwave", str(e)[:200], "Check your internet")
    if not os.getenv("FLW_SECRET_HASH"):
        print(f"{Y}  NOTE{X}  FLW_SECRET_HASH empty — fine on your laptop; set it when the site is online")
else:
    warn("Flutterwave", "FLW_SECRET_KEY empty — payments run in test mode")

# ── 6. Every page ───────────────────────────────────────────
print(f"\n{B}6. Opening every page{X}")
if A:
    c = A.app.test_client()
    pages = ["/", "/about", "/academics", "/facilities", "/admissions", "/admissions/status", "/news", "/gallery",
             "/contact", "/result", "/login"]
    fails = []
    for p in pages:
        code = c.get(p).status_code
        if code != 200:
            fails.append(f"{p} ({code})")
    with A.app.app_context():
        admin = A.User.query.filter_by(role="admin").first()
        for post in A.Post.query.limit(3):
            if c.get(f"/news/{post.slug}").status_code != 200:
                fails.append(f"/news/{post.slug}")
    with c.session_transaction() as s:
        s["uid"] = admin.id
    admin_pages = ["/admin", "/admin/settings", "/admin/students", "/admin/teachers", "/admin/classes", "/admin/subjects",
                   "/admin/results", "/admin/attendance", "/admin/timetable", "/admin/promote", "/admin/idcards", "/admin/fees",
                   "/admin/payments", "/admin/pins", "/admin/admissions", "/admin/messages", "/admin/posts", "/admin/gallery",
                   "/admin/testimonials", "/admin/website", "/admin/announcements", "/admin/emails"]
    for p in admin_pages:
        code = c.get(p).status_code
        if code != 200:
            fails.append(f"{p} ({code})")
    total = len(pages) + len(admin_pages)
    if fails:
        bad("Pages", "these failed: " + ", ".join(fails), "Send this list to your developer")
    else:
        ok("All pages open", f"({total} public + admin pages)")

# ── Summary ─────────────────────────────────────────────────
passed = sum(1 for r in results if r[0]); failed = [r[1] for r in results if not r[0]]
print(f"\n{B}Result: {passed} passed, {len(failed)} failed{X}")
if failed:
    print(f"{R}Fix: {', '.join(failed)}{X}\n")
else:
    print(f"{G}Everything is connected. Next: try the full flow by hand (apply → enrol → pay → results).{X}\n")
