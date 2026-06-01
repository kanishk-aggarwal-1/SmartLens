from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from ..settings import settings
from .security import decrypt_secret, encrypt_secret


@dataclass
class UserRecord:
    id: str
    email: str
    password_hash: str
    google_maps_api_key_encrypted: str | None
    gemini_api_key_encrypted: str | None

    @property
    def google_maps_api_key(self) -> str:
        return decrypt_secret(self.google_maps_api_key_encrypted) or settings.GOOGLE_MAPS_API_KEY

    @property
    def gemini_api_key(self) -> str | None:
        return decrypt_secret(self.gemini_api_key_encrypted) or settings.GEMINI_API_KEY


def _is_postgres() -> bool:
    return settings.DATABASE_URL.startswith(("postgres://", "postgresql://"))


def _sqlite_path() -> str:
    parsed = urlparse(settings.DATABASE_URL)
    if parsed.scheme != "sqlite":
        return "./smartlens.db"
    if parsed.path in {"", "/"}:
        return "./smartlens.db"
    if parsed.netloc:
        return f"//{parsed.netloc}{parsed.path}"
    return parsed.path.lstrip("/") or "./smartlens.db"


def _connect():
    if _is_postgres():
        import psycopg

        return psycopg.connect(settings.DATABASE_URL)

    path = Path(_sqlite_path())
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _placeholder() -> str:
    return "%s" if _is_postgres() else "?"


def init_user_store() -> None:
    if _is_postgres():
        sql = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            google_maps_api_key_encrypted TEXT,
            gemini_api_key_encrypted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    else:
        sql = """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            google_maps_api_key_encrypted TEXT,
            gemini_api_key_encrypted TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    conn = _connect()
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _row_to_user(row: Any | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        id=row["id"] if not isinstance(row, tuple) else row[0],
        email=row["email"] if not isinstance(row, tuple) else row[1],
        password_hash=row["password_hash"] if not isinstance(row, tuple) else row[2],
        google_maps_api_key_encrypted=(
            row["google_maps_api_key_encrypted"] if not isinstance(row, tuple) else row[3]
        ),
        gemini_api_key_encrypted=(
            row["gemini_api_key_encrypted"] if not isinstance(row, tuple) else row[4]
        ),
    )


def get_user_by_email(email: str) -> UserRecord | None:
    marker = _placeholder()
    conn = _connect()
    try:
        cursor = conn.execute(
            f"""
            SELECT id, email, password_hash, google_maps_api_key_encrypted, gemini_api_key_encrypted
            FROM users
            WHERE lower(email) = lower({marker})
            """,
            (email.strip(),),
        )
        return _row_to_user(cursor.fetchone())
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> UserRecord | None:
    marker = _placeholder()
    conn = _connect()
    try:
        cursor = conn.execute(
            f"""
            SELECT id, email, password_hash, google_maps_api_key_encrypted, gemini_api_key_encrypted
            FROM users
            WHERE id = {marker}
            """,
            (user_id,),
        )
        return _row_to_user(cursor.fetchone())
    finally:
        conn.close()


def create_user(
    *,
    email: str,
    password_hash: str,
    google_maps_api_key: str,
    gemini_api_key: str | None,
) -> UserRecord:
    now = datetime.now(timezone.utc).isoformat()
    user_id = str(uuid4())
    marker = _placeholder()
    conn = _connect()
    try:
        conn.execute(
            f"""
            INSERT INTO users (
                id,
                email,
                password_hash,
                google_maps_api_key_encrypted,
                gemini_api_key_encrypted,
                created_at,
                updated_at
            )
            VALUES ({marker}, {marker}, {marker}, {marker}, {marker}, {marker}, {marker})
            """,
            (
                user_id,
                email.strip().lower(),
                password_hash,
                encrypt_secret(google_maps_api_key),
                encrypt_secret(gemini_api_key),
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    user = get_user_by_id(user_id)
    if not user:
        raise RuntimeError("User was created but could not be loaded.")
    return user


def update_user_keys(
    *,
    user_id: str,
    google_maps_api_key: str,
    gemini_api_key: str | None,
) -> UserRecord:
    now = datetime.now(timezone.utc).isoformat()
    marker = _placeholder()
    conn = _connect()
    try:
        conn.execute(
            f"""
            UPDATE users
            SET google_maps_api_key_encrypted = {marker},
                gemini_api_key_encrypted = {marker},
                updated_at = {marker}
            WHERE id = {marker}
            """,
            (
                encrypt_secret(google_maps_api_key),
                encrypt_secret(gemini_api_key),
                now,
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    user = get_user_by_id(user_id)
    if not user:
        raise RuntimeError("User was not found.")
    return user
