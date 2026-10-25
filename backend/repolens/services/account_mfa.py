"""Second-factor checks shared by sign-in and the account endpoints."""

from sqlalchemy.orm import Session

from ..models import User
from ..security import decrypt_secret
from . import totp


def accept(db: Session, user: User, code: str) -> bool:
    """True for a valid authenticator code (once per 30-second step) or an unused recovery code (once, ever)."""
    code = code.strip()
    if user.totp_secret_encrypted:
        step = totp.verify(decrypt_secret(user.totp_secret_encrypted), code, user.totp_last_step)
        if step is not None:
            user.totp_last_step = step
            db.commit()
            return True
    h = totp.hash_code(code)
    if h in (user.recovery_codes or []):
        user.recovery_codes = [c for c in user.recovery_codes if c != h]
        db.commit()
        return True
    return False
