"""Set the single-user login (password never echoed or printed).
    cd backend && uv run python ../scripts/set_password.py                  # prompt
    cd backend && uv run python ../scripts/set_password.py --from-keychain  # read macOS Keychain item "job-hunter-ai"
Passwords under 12 characters are refused unless you pass --allow-short (prints a warning).
Writes ADMIN_PASSWORD_HASH (and JWT_SECRET if missing) into ../.env."""
import getpass
import re
import secrets
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, ".")
from app.core.security import hash_password  # noqa: E402

MIN_LEN = 12
ALLOW_SHORT = "--allow-short" in sys.argv
env = Path("../.env")
text = env.read_text()
if "--from-keychain" in sys.argv:
    r = subprocess.run(["security", "find-generic-password", "-s", "job-hunter-ai", "-w"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("Keychain item 'job-hunter-ai' not found; nothing changed.")
    pw = r.stdout.rstrip("\n")
    if len(pw) < MIN_LEN and not ALLOW_SHORT:
        sys.exit(f"Keychain password shorter than {MIN_LEN} characters; nothing changed (use --allow-short to override).")
else:
    pw = getpass.getpass("New password (min 12 chars): ")
    if (len(pw) < MIN_LEN and not ALLOW_SHORT) or pw != getpass.getpass("Repeat: "):
        sys.exit("Too short or mismatch; nothing changed.")


def put(text: str, key: str, val: str) -> str:
    line = f"{key}='{val}'"  # single quotes: '$' in argon2 hashes must stay literal for compose/dotenv
    if re.search(rf"^{key}=.*$", text, re.M):
        return re.sub(rf"^{key}=.*$", lambda _: line, text, flags=re.M)
    return text.rstrip("\n") + "\n" + line + "\n"


if len(pw) < MIN_LEN:
    print(f"WARNING: password is shorter than {MIN_LEN} characters (allowed by --allow-short). Keep the app on localhost.")
text = put(text, "ADMIN_PASSWORD_HASH", hash_password(pw))
if not re.search(r"^JWT_SECRET='?\S{32,}", text, re.M):
    text = put(text, "JWT_SECRET", secrets.token_urlsafe(48))
env.write_text(text)
env.chmod(0o600)
print("Saved to .env")
