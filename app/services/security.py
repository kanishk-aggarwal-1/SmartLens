from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from fastapi import HTTPException, Request, Response

from ..settings import settings
from .monitoring import metrics


HASH_ALGORITHM = "pbkdf2_sha256"
HASH_ITERATIONS = 260000


def hash_secret(secret: str, *, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        secret.encode("utf-8"),
        salt,
        HASH_ITERATIONS,
    )
    return "$".join(
        [
            HASH_ALGORITHM,
            str(HASH_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_secret(secret: str, stored_hash: str | None) -> bool:
    if not secret or not stored_hash:
        return False

    try:
        algorithm, raw_iterations, raw_salt, raw_digest = stored_hash.split("$", 3)
        if algorithm != HASH_ALGORITHM:
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(raw_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            secret.encode("utf-8"),
            salt,
            int(raw_iterations),
        )
    except Exception:
        return False

    return hmac.compare_digest(actual, expected)


def _hashes_from_csv(value: str) -> Iterable[str]:
    for item in (value or "").split(","):
        item = item.strip()
        if item:
            yield item


def verify_api_key(api_key: str | None) -> bool:
    return any(verify_secret(api_key or "", item) for item in _hashes_from_csv(settings.API_KEY_HASHES))


def generate_encryption_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def _fernet():
    from cryptography.fernet import Fernet

    key = settings.SECRET_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("SECRET_ENCRYPTION_KEY is required to store provider keys.")
    return Fernet(key.encode("ascii"))


def encrypt_secret(secret: str | None) -> str | None:
    if not secret:
        return None
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(encrypted_secret: str | None) -> str | None:
    if not encrypted_secret:
        return None
    return _fernet().decrypt(encrypted_secret.encode("ascii")).decode("utf-8")


def _sign_session_payload(payload: str) -> str:
    secret = settings.SESSION_SECRET_KEY or ""
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(username: str) -> str:
    expires_at = int(time.time() + settings.SESSION_TTL_S)
    payload = f"{username}:{expires_at}"
    signature = _sign_session_payload(payload)
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode("utf-8")).decode("ascii")


def read_session_token(token: str | None) -> str | None:
    if not token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, raw_expires_at, signature = decoded.rsplit(":", 2)
        payload = f"{username}:{raw_expires_at}"
        expected = _sign_session_payload(payload)
        if not hmac.compare_digest(signature, expected):
            return None
        if int(raw_expires_at) < int(time.time()):
            return None
        return username
    except Exception:
        return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "smartlens_session",
        token,
        max_age=settings.SESSION_TTL_S,
        httponly=True,
        secure=settings.SECURE_COOKIES,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie("smartlens_session", httponly=True, secure=settings.SECURE_COOKIES, samesite="lax")


@dataclass
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_s: int


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, *, limit: int, window_s: int = 60) -> RateLimitResult:
        now = time.time()
        with self._lock:
            events = self._events[key]
            while events and events[0] <= now - window_s:
                events.popleft()

            if len(events) >= limit:
                retry_after = max(1, int(window_s - (now - events[0])))
                return RateLimitResult(False, 0, retry_after)

            events.append(now)
            return RateLimitResult(True, max(0, limit - len(events)), 0)


rate_limiter = InMemoryRateLimiter()


def client_rate_key(request: Request, scope: str) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",", 1)[0].strip()
    if not client_ip and request.client:
        client_ip = request.client.host
    return f"{scope}:{client_ip or 'unknown'}"


def enforce_rate_limit(request: Request, *, scope: str, limit: int) -> None:
    if limit <= 0:
        return
    result = rate_limiter.check(client_rate_key(request, scope), limit=limit)
    if result.allowed:
        return

    metrics.increment("rate_limited", tags={"scope": scope})
    raise HTTPException(
        status_code=429,
        detail={"code": "RATE_LIMITED", "message": "Too many requests.", "retryable": True},
        headers={"Retry-After": str(result.retry_after_s)},
    )


def authenticated_username(request: Request) -> str | None:
    token = request.cookies.get("smartlens_session")
    username = read_session_token(token)
    if username:
        return username

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        api_key = authorization.split(" ", 1)[1].strip()
        if verify_api_key(api_key):
            return "api-key"

    if verify_api_key(request.headers.get("x-api-key")):
        return "api-key"

    return None


def require_authenticated_request(request: Request) -> str:
    if not settings.REQUIRE_AUTH:
        return "anonymous"

    username = authenticated_username(request)
    if username:
        return username

    metrics.increment("auth_failed", tags={"path": request.url.path})
    raise HTTPException(
        status_code=401,
        detail={"code": "AUTH_REQUIRED", "message": "Authentication is required.", "retryable": False},
    )
