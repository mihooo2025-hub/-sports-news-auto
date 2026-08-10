"""
db.py
=====
قاعدة بيانات SQLite محلية لتسجيل الأخبار التي تمت معالجتها،
لمنع نشر نفس الخبر أكثر من مرة.
"""

import sqlite3
import os
import hashlib
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "news.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url_hash TEXT UNIQUE NOT NULL,
            original_url TEXT,
            title TEXT,
            wp_post_id INTEGER,
            status TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def is_processed(url: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_news WHERE url_hash = ?", (_hash_url(url),))
    result = cur.fetchone()
    conn.close()
    return result is not None


def mark_processed(url: str, title: str = "", wp_post_id: int = None, status: str = "published"):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO processed_news (url_hash, original_url, title, wp_post_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_hash_url(url), url, title, wp_post_id, status, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # مسجل بالفعل
    finally:
        conn.close()


init_db()
