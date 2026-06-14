import os
import sqlite3
import threading
import logging
from datetime import datetime, date
from typing import Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

_local = threading.local()


def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def init_db() -> None:
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            total_downloads INTEGER DEFAULT 0,
            total_file_size INTEGER DEFAULT 0,
            daily_limit_mb INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            daily_files_today INTEGER DEFAULT 0,
            daily_files_date TEXT,
            downloading INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT,
            file_type TEXT,
            file_size INTEGER,
            filepath TEXT,
            filename TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS premium (
            user_id INTEGER PRIMARY KEY,
            plan_type TEXT,
            expires_at TEXT,
            stars_spent INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            telegram_payment_charge_id TEXT,
            stars_amount INTEGER,
            plan_type TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    logger.info("Database initialized at %s", DB_PATH)
    _migrate()


def _migrate() -> None:
    conn = _get_db()
    for col in ("daily_files_today", "daily_files_date", "downloading"):
        try:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def upsert_user(user_id: int, username: str, first_name: str) -> None:
    conn = _get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO users (user_id, username, first_name, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_seen = excluded.last_seen
    """, (user_id, username, first_name, now, now))
    conn.commit()


def log_download(user_id: int, url: str, file_type: str, file_size: int, filepath: str, filename: str) -> None:
    conn = _get_db()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO downloads (user_id, url, file_type, file_size, filepath, filename, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, url, file_type, file_size, filepath, filename, now))
    conn.execute("""
        UPDATE users SET
            total_downloads = total_downloads + 1,
            total_file_size = total_file_size + ?,
            daily_files_today = daily_files_today + 1,
            daily_files_date = ?
        WHERE user_id = ?
    """, (file_size, date.today().isoformat(), user_id))
    conn.commit()


def get_user_daily_size(user_id: int) -> int:
    conn = _get_db()
    today = date.today().isoformat()
    row = conn.execute("""
        SELECT COALESCE(SUM(file_size), 0) as total
        FROM downloads
        WHERE user_id = ? AND date(created_at) = ?
    """, (user_id, today)).fetchone()
    return row["total"] if row else 0


def get_user_daily_files(user_id: int) -> int:
    conn = _get_db()
    row = conn.execute(
        "SELECT daily_files_today FROM users WHERE user_id = ? AND daily_files_date = ?",
        (user_id, date.today().isoformat()),
    ).fetchone()
    return row["daily_files_today"] if row else 0


def reset_daily_counts(user_id: int) -> None:
    conn = _get_db()
    conn.execute(
        "UPDATE users SET daily_files_today = 0, daily_files_date = ? WHERE user_id = ?",
        ("", user_id),
    )
    conn.commit()


def get_total_downloads() -> int:
    conn = _get_db()
    row = conn.execute("SELECT COUNT(*) as cnt FROM downloads").fetchone()
    return row["cnt"] if row else 0


def set_user_limit(mb: int) -> None:
    conn = _get_db()
    conn.execute("""
        INSERT INTO config (key, value) VALUES ('daily_limit_mb', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (str(mb),))
    conn.commit()


def get_user_limit(user_id: int = 0) -> int:
    conn = _get_db()
    from config import FREE_DAILY_MB
    row = conn.execute("SELECT value FROM config WHERE key = 'daily_limit_mb'").fetchone()
    return int(row["value"]) if row else FREE_DAILY_MB


def get_recent_download(url: str, seconds: int = 300) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("""
        SELECT * FROM downloads
        WHERE url = ? AND created_at > datetime('now', ?)
        ORDER BY created_at DESC LIMIT 1
    """, (url, f"-{seconds} seconds")).fetchone()
    if row:
        return dict(row)
    return None


def is_user_blocked(user_id: int) -> bool:
    conn = _get_db()
    row = conn.execute("SELECT is_blocked FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["is_blocked"] == 1 if row else False


def block_user(user_id: int) -> None:
    conn = _get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.execute("UPDATE users SET is_blocked = 1 WHERE user_id = ?", (user_id,))
    conn.commit()


def unblock_user(user_id: int) -> None:
    conn = _get_db()
    conn.execute("UPDATE users SET is_blocked = 0 WHERE user_id = ?", (user_id,))
    conn.commit()


def get_blocked_users() -> list:
    conn = _get_db()
    return [dict(r) for r in conn.execute(
        "SELECT user_id, username, first_name FROM users WHERE is_blocked = 1"
    ).fetchall()]


# --- Premium ---

def is_premium(user_id: int) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT expires_at FROM premium WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return user_id == 7946767855  # admin always premium
    try:
        expires = datetime.fromisoformat(row["expires_at"])
        return expires > datetime.utcnow()
    except Exception:
        return False


def set_premium(user_id: int, plan_type: str, days: int, stars: int, charge_id: str) -> None:
    conn = _get_db()
    expires = datetime.utcnow()
    if days == 1:
        expires = expires.replace(hour=23, minute=59, second=59)
    else:
        from datetime import timedelta
        expires += timedelta(days=days)
    expires_str = expires.isoformat()
    conn.execute("""
        INSERT INTO premium (user_id, plan_type, expires_at, stars_spent)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            plan_type = excluded.plan_type,
            expires_at = excluded.expires_at,
            stars_spent = stars_spent + excluded.stars_spent
    """, (user_id, plan_type, expires_str, stars))
    conn.execute("""
        INSERT INTO transactions (user_id, telegram_payment_charge_id, stars_amount, plan_type, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, charge_id, stars, plan_type, datetime.utcnow().isoformat()))
    conn.commit()


def get_premium_info(user_id: int) -> Optional[dict]:
    conn = _get_db()
    row = conn.execute("SELECT * FROM premium WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# --- Queue ---

def is_downloading(user_id: int) -> bool:
    conn = _get_db()
    row = conn.execute(
        "SELECT downloading FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return bool(row and row["downloading"])


def set_downloading(user_id: int, status: bool) -> None:
    conn = _get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.execute("UPDATE users SET downloading = ? WHERE user_id = ?", (int(status), user_id))
    conn.commit()


# --- Stats ---

def get_stats() -> dict:
    conn = _get_db()
    total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    total_downloads = conn.execute("SELECT COALESCE(SUM(total_downloads), 0) as c FROM users").fetchone()["c"]
    total_size = conn.execute("SELECT COALESCE(SUM(total_file_size), 0) as c FROM users").fetchone()["c"]
    today = date.today().isoformat()
    today_users = conn.execute(
        "SELECT COUNT(DISTINCT user_id) as c FROM downloads WHERE date(created_at) = ?",
        (today,),
    ).fetchone()["c"]
    today_downloads = conn.execute(
        "SELECT COUNT(*) as c FROM downloads WHERE date(created_at) = ?",
        (today,),
    ).fetchone()["c"]
    videos = conn.execute("SELECT COUNT(*) as c FROM downloads WHERE file_type = 'video'").fetchone()["c"]
    photos = conn.execute("SELECT COUNT(*) as c FROM downloads WHERE file_type = 'photo'").fetchone()["c"]
    premium_count = conn.execute("SELECT COUNT(*) as c FROM premium").fetchone()["c"]
    return {
        "total_users": total_users,
        "total_downloads": total_downloads,
        "total_size_mb": round(total_size / 1024 / 1024, 1),
        "today_users": today_users,
        "today_downloads": today_downloads,
        "videos": videos,
        "photos": photos,
        "premium_count": premium_count,
    }


def get_all_users() -> list:
    conn = _get_db()
    return [dict(r) for r in conn.execute(
        "SELECT user_id, username, first_name, first_seen, last_seen, "
        "total_downloads, total_file_size, is_blocked FROM users ORDER BY last_seen DESC"
    ).fetchall()]
