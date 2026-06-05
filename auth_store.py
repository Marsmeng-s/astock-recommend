"""账号存储：SQLite + 首次登录 7 天有效期"""
from __future__ import annotations

import os
import secrets
import sqlite3
import string
import threading
from datetime import datetime, timedelta
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

import config
import paths

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db_path() -> str:
    data_dir = os.path.join(paths.app_dir(), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "accounts.db")


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_db_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _format_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _random_password(length: int = 8) -> str:
    chars = string.ascii_letters + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def _user_status(row: sqlite3.Row) -> str:
    if not row["enabled"]:
        return "已禁用"
    if not row["first_login_at"]:
        return "未激活"
    expires = _parse_dt(row["expires_at"])
    if expires and datetime.now() >= expires:
        return "已过期"
    return "有效"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "username": row["username"],
        "first_login_at": row["first_login_at"],
        "expires_at": row["expires_at"],
        "last_login_at": row["last_login_at"],
        "enabled": bool(row["enabled"]),
        "status": _user_status(row),
        "note": row["note"] or "",
    }


def init_database() -> str | None:
    """初始化数据库；首次运行创建 50 个账号。返回初始账号文件路径（仅首次）。"""
    with _lock:
        conn = _connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                first_login_at TEXT,
                expires_at TEXT,
                last_login_at TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                note TEXT DEFAULT ''
            )
            """
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count > 0:
            return None

        credentials: list[tuple[str, str]] = []
        for i in range(1, config.INITIAL_ACCOUNT_COUNT + 1):
            username = f"{config.ACCOUNT_PREFIX}{i:03d}"
            password = _random_password()
            conn.execute(
                "INSERT INTO users (username, password_hash, enabled) VALUES (?, ?, 1)",
                (username, generate_password_hash(password)),
            )
            credentials.append((username, password))
        conn.commit()

        export_path = os.path.join(os.path.dirname(_db_path()), "initial_accounts.txt")
        with open(export_path, "w", encoding="utf-8") as f:
            f.write("A股智能推荐 · 初始账号（请妥善保管，首次登录后 7 天内有效）\n")
            f.write("=" * 56 + "\n\n")
            for username, password in credentials:
                f.write(f"账号: {username}\n密码: {password}\n\n")
            f.write(f"管理后台: http://<服务器IP>:{config.WEB_PORT}/admin\n")
            f.write(f"管理员账号: {config.ADMIN_USERNAME}\n")
            f.write(f"管理员密码: {config.ADMIN_PASSWORD}\n")
        return export_path


def authenticate_user(username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
    """验证用户；首次登录自动激活 7 天有效期。"""
    username = (username or "").strip()
    if not username or not password:
        return None, "请输入账号和密码"

    with _lock:
        conn = _connect()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return None, "账号或密码错误"
        if not row["enabled"]:
            return None, "账号已禁用，请联系管理员"

        now = datetime.now()
        now_s = _format_dt(now)

        if row["first_login_at"]:
            expires = _parse_dt(row["expires_at"])
            if expires and now >= expires:
                return None, "账号已过期，请联系管理员续期"
        else:
            expires = now + timedelta(days=config.ACCOUNT_VALID_DAYS)
            conn.execute(
                """
                UPDATE users
                SET first_login_at = ?, expires_at = ?, last_login_at = ?
                WHERE username = ?
                """,
                (now_s, _format_dt(expires), now_s, username),
            )
            conn.commit()
        if row["first_login_at"]:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE username = ?",
                (now_s, username),
            )
            conn.commit()

        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()

    return _row_to_dict(row), None


def get_user(username: str) -> dict[str, Any] | None:
    with _lock:
        row = _connect().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def is_user_valid(username: str) -> bool:
    user = get_user(username)
    if not user or not user["enabled"]:
        return False
    if not user["first_login_at"]:
        return True
    expires = _parse_dt(user["expires_at"])
    return expires is None or datetime.now() < expires


def list_users() -> list[dict[str, Any]]:
    with _lock:
        rows = _connect().execute(
            "SELECT * FROM users ORDER BY username"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def set_expires_at(username: str, expires_at: str) -> bool:
    with _lock:
        cur = _connect().execute(
            "UPDATE users SET expires_at = ? WHERE username = ?",
            (expires_at, username),
        )
        _connect().commit()
        return cur.rowcount > 0


def extend_days(username: str, days: int) -> bool:
    with _lock:
        row = _connect().execute(
            "SELECT expires_at, first_login_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if not row:
            return False
        base = _parse_dt(row["expires_at"]) or datetime.now()
        if base < datetime.now():
            base = datetime.now()
        new_exp = _format_dt(base + timedelta(days=days))
        _connect().execute(
            "UPDATE users SET expires_at = ? WHERE username = ?",
            (new_exp, username),
        )
        if not row["first_login_at"]:
            now_s = _now_str()
            _connect().execute(
                """
                UPDATE users SET first_login_at = ?, expires_at = ?
                WHERE username = ?
                """,
                (now_s, new_exp, username),
            )
        _connect().commit()
        return True


def reset_activation(username: str) -> bool:
    """清除首次登录记录，下次登录重新计算 7 天。"""
    with _lock:
        cur = _connect().execute(
            """
            UPDATE users
            SET first_login_at = NULL, expires_at = NULL, last_login_at = NULL
            WHERE username = ?
            """,
            (username,),
        )
        _connect().commit()
        return cur.rowcount > 0


def set_enabled(username: str, enabled: bool) -> bool:
    with _lock:
        cur = _connect().execute(
            "UPDATE users SET enabled = ? WHERE username = ?",
            (1 if enabled else 0, username),
        )
        _connect().commit()
        return cur.rowcount > 0


def set_password(username: str, password: str) -> bool:
    with _lock:
        cur = _connect().execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (generate_password_hash(password), username),
        )
        _connect().commit()
        return cur.rowcount > 0


def verify_admin(username: str, password: str) -> bool:
    return (
        username == config.ADMIN_USERNAME
        and password == config.ADMIN_PASSWORD
    )
