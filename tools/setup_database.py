"""Connects the app to your Neon database and prepares the cloud secrets.

Run from the repo root:

    python tools/setup_database.py

It asks for your Neon connection string (hidden as you paste), then:
  1. connects to Neon directly,
  2. runs the app's own start-up code against it (creates tables, adds the doctors),
  3. if you answer y, saves the settings into .env and .env.cloud.

Secrets are never printed, and error messages have the password blanked out.
"""

import getpass
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- helpers

def prepare_url(raw):
    """Pull the connection string out of whatever was pasted and make it driver-ready."""
    match = re.search(r"postgres(?:ql)?://[^\s'\"]+", raw or "")
    if not match:
        return None
    url = match.group(0).rstrip(";")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    if not any(k == "sslmode" for k, _ in query):
        query.append(("sslmode", "require"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def mask(text, url):
    password = urlsplit(url).password
    return str(text).replace(password, "***") if password else str(text)


def set_env_value(path, key, value):
    """Replace `key=...` in an env file (or append it). Keeps the file's line endings."""
    if not path.exists():
        path.write_text("", encoding="utf-8")
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[^\r\n]*", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _m: f"{key}={value}", text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += newline
        text += f"{key}={value}{newline}"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def test_direct(url):
    from sqlalchemy import create_engine, text
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            version = conn.execute(text("select version()")).scalar()
            zone = conn.execute(text("show timezone")).scalar()
        return True, f"{version.split(',')[0]} (database timezone: {zone})"
    except Exception as exc:
        return False, mask(f"{type(exc).__name__}: {str(exc).strip()[:400]}", url)
    finally:
        engine.dispose()


def test_app(url):
    """Run the app's own start-up code against the new database."""
    try:
        from backend.database.connection import SessionLocal, init_db
        from backend.database.models import Appointment, Doctor
        init_db()
        with SessionLocal() as db:
            doctors = db.query(Doctor).order_by(Doctor.doctor_id).all()
            appointments = db.query(Appointment).count()
            lines = [f"{d.doctor_id}  {d.doctor_name}  ({d.specialization}, "
                     f"{d.working_hours_start}-{d.working_hours_end})" for d in doctors]
        bind = SessionLocal.kw.get("bind")
        pre_ping = getattr(getattr(bind, "pool", None), "_pre_ping", None)
        return True, {"doctors": lines, "appointments": appointments, "pre_ping": pre_ping}
    except Exception as exc:
        return False, mask(f"{type(exc).__name__}: {str(exc).strip()[:600]}", url)


def save_settings(url):
    from config.settings import settings

    shared = {
        "DATABASE_URL": url,
        "GOOGLE_CALENDAR_SANDBOX": "false",
        "CLINIC_TIMEZONE": "Asia/Karachi",
    }
    calendar_id = (settings.GOOGLE_CALENDAR_ID or "").strip() or "primary"
    google = {
        "GOOGLE_CLIENT_ID": settings.GOOGLE_CLIENT_ID,
        "GOOGLE_CLIENT_SECRET": settings.GOOGLE_CLIENT_SECRET,
        "GOOGLE_REFRESH_TOKEN": settings.GOOGLE_REFRESH_TOKEN,
        "GOOGLE_CALENDAR_ID": calendar_id,
    }
    for key, value in shared.items():
        set_env_value(ROOT / ".env", key, value)
        set_env_value(ROOT / ".env.cloud", key, value)
    set_env_value(ROOT / ".env", "GOOGLE_CALENDAR_ID", calendar_id)
    for key, value in google.items():
        set_env_value(ROOT / ".env.cloud", key, value)
    missing = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
               if not google[k]]
    return missing


# ---------------------------------------------------------------- main

def main():
    raw = getpass.getpass("Paste your Neon connection string (it stays hidden), then Enter: ")
    url = prepare_url(raw)
    if not url:
        print("That does not look like a connection string. It should start with postgresql://")
        return 1
    os.environ["DATABASE_URL"] = url  # the app reads this instead of .env for this run

    print("\n1) Connecting to Neon ...")
    ok, info = test_direct(url)
    print(("   OK: " if ok else "   FAILED: ") + info)
    if not ok:
        print("   Check the password and that you copied the whole string. "
              "A sleeping Neon database can need a second try.")
        return 1

    print("\n2) Running the app's own database start-up against Neon ...")
    ok, info = test_app(url)
    if not ok:
        print("   FAILED: " + info)
        print("   Send me this message and the contents of backend/database/connection.py.")
        return 1
    print(f"   OK: tables ready, {info['appointments']} appointment(s) stored. Doctors:")
    for line in info["doctors"]:
        print("     " + line)
    if info["pre_ping"] is False:
        print("\n   NOTE: the app's database engine does not re-check connections. Neon puts idle\n"
              "   databases to sleep, so the first request after a pause could fail. Send me\n"
              "   backend/database/connection.py and I will add the one-line fix.")
    elif info["pre_ping"] is True:
        print("   Connection re-checking is ON (good for Neon).")

    answer = input("\nSave these settings into .env and .env.cloud? [y/N] ").strip().lower()
    if answer != "y":
        print("Nothing saved.")
        return 0
    missing = save_settings(url)
    print("\nSaved: DATABASE_URL, GOOGLE_CALENDAR_SANDBOX=false, CLINIC_TIMEZONE=Asia/Karachi "
          "(both files) and the Google values (.env.cloud).")
    if missing:
        print("WARNING: still empty in .env: " + ", ".join(missing))
    print("\nNext: python tools/check_calendar.py --write   then redeploy the agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())