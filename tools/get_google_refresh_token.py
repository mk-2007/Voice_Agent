"""Creates a new Google refresh token and saves it into .env and .env.cloud.

Run from the repo root, AFTER publishing your OAuth app ("In production"):

    python tools/get_google_refresh_token.py

A browser opens. Sign in with the Google account that owns the calendar and
approve access. The token is written straight into your env files and is never
printed, so it cannot leak into a screenshot or a chat.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCOPES = ["https://www.googleapis.com/auth/calendar"]  # same scope the app uses
PORT = 8080
KEY = "GOOGLE_REFRESH_TOKEN"


def set_env_value(path: Path, key: str, value: str) -> bool:
    """Replace `key=...` in an env file (or append it). Keeps the file's line endings."""
    if not path.exists():
        return False
    with open(path, encoding="utf-8", newline="") as handle:  # newline="" keeps CRLF as-is
        text = handle.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[^\r\n]*", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(lambda _m: f"{key}={value}", text, count=1)
    else:
        if text and not text.endswith(("\n", "\r\n")):
            text += newline
        text += f"{key}={value}{newline}"
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return True


def main() -> int:
    from config.settings import settings

    if not (settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET):
        print("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are missing in .env")
        return 1

    from google_auth_oauthlib.flow import InstalledAppFlow

    config = {"installed": {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [f"http://localhost:{PORT}/"],
    }}
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    print(f"Opening your browser. If Google shows redirect_uri_mismatch, add\n"
          f"  http://localhost:{PORT}/\nunder Authorized redirect URIs of your OAuth client and run again.\n")
    creds = flow.run_local_server(port=PORT, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print("Google did not return a refresh token. Remove this app's access at "
              "https://myaccount.google.com/permissions and run the script again.")
        return 1

    for name in (".env", ".env.cloud"):
        done = set_env_value(ROOT / name, KEY, creds.refresh_token)
        print(f"{name}: {'updated' if done else 'not found, skipped'}")
    print(f"\nNew refresh token saved ({len(creds.refresh_token)} characters). "
          "Now run:  python tools/check_calendar.py --write")
    return 0


if __name__ == "__main__":
    sys.exit(main())