import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet

from .config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(user_id: str, org_id: str, role: str) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "org": org_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])


def _fernet() -> Fernet:
    s = get_settings()
    key = s.secret_key
    if not key:
        key = base64.urlsafe_b64encode(hashlib.sha256(s.jwt_secret.encode()).digest()).decode()
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


_SECRET_PATTERNS = None


def redact_secrets(text: str) -> str:
    """Strip likely credentials before text goes into logs or LLM prompts."""
    import re

    global _SECRET_PATTERNS
    if _SECRET_PATTERNS is None:
        _SECRET_PATTERNS = [
            re.compile(r"(?i)(api[_-]?key|secret|token|passwd|password)(\s*[:=]\s*)(['\"]?)[^\s'\"]{6,}\3"),
            re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
            re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
            re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        ]
    out = text
    out = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
    for p in _SECRET_PATTERNS[1:]:
        out = p.sub("[REDACTED]", out)
    return out


_STORED_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
]
_URL_CREDS = re.compile(r"(://[^/\s:@\"']+:)[^@\s/\"']{3,}(@)")
# a quoted literal assigned to a secret-looking name; unquoted values (function calls, variables) are left alone
_QUOTED_SECRET = re.compile(
    r"(?i)\b((?:[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|private[_-]?key)[\w-]*|token))"
    r"(\s*[:=]\s*)([\"'])[^\"'\s]{12,}\3"
)


def redact_stored(text: str) -> tuple[str, int]:
    """High-confidence secret removal for text we persist and display (chunks, diffs, PR text).

    Deliberately narrower than `redact_secrets`: code like `token = get_token()` must survive so citations and search
    still match the source. Returns (redacted_text, number_of_redactions).
    """
    count = 0

    def sub(pattern: re.Pattern, repl, s: str) -> str:
        nonlocal count
        new, n = pattern.subn(repl, s)
        count += n
        return new

    out = text
    for p in _STORED_PATTERNS:
        out = sub(p, "[REDACTED]", out)
    out = sub(_URL_CREDS, r"\1[REDACTED]\2", out)
    out = sub(_QUOTED_SECRET, lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[REDACTED]{m.group(3)}", out)
    return out, count
