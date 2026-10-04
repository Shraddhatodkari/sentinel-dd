"""Password hashing (PBKDF2-HMAC-SHA256, 600k iterations per OWASP guidance) and password policy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

ITERATIONS = 600_000


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_b64), int(iters))
    return hmac.compare_digest(dk, base64.b64decode(hash_b64))


def password_problems(password: str, min_length: int = 12, username: str = "") -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"must be at least {min_length} characters")
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        problems.append("must contain at least three of: lowercase, uppercase, digit, symbol")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    return problems


DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password", iterations=ITERATIONS)
