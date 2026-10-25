"""A password policy that blocks the guesses attackers try first, without composition rules that annoy people."""

import re

_COMMON = frozenset(
    """password password1 password12 password123 passw0rd p@ssw0rd p@ssword 12345678 123456789 1234567890 11111111
    00000000 qwertyui qwerty123 qwertyuiop 1q2w3e4r 1qaz2wsx abc12345 iloveyou letmein1 welcome1 welcome123 admin123
    administrator changeme changeme1 trustno1 football1 monkey123 dragon123 sunshine1 princess1 baseball1 superman1
    master123 hello123 shadow123 michael1 charlie1 123qweasd zaq12wsx asdfghjk zxcvbnm1 test1234 testtest repolens
    repolens1 repolens123""".split()
)


def check_password(password: str, email: str = "") -> str | None:
    """None when acceptable, otherwise a sentence saying what to change."""
    if len(password) < 8:
        return "Use at least 8 characters."
    if len(password.encode()) > 72:
        return "Use at most 72 bytes (a longer password would be silently cut short)."
    low = password.lower()
    squashed = re.sub(r"[^a-z0-9]", "", low)
    if low in _COMMON or squashed in _COMMON:
        return "That password is on the list of the most common ones. Choose something less guessable."
    if len(set(password)) < 4:
        return "Use more than a few different characters."
    local = re.sub(r"[^a-z0-9]", "", email.split("@")[0].lower())
    if len(local) >= 4 and local in squashed:
        return "Do not use your e-mail name in your password."
    return None
