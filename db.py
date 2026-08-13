"""
db.py
=====
قاعدة بيانات SQLite محلية لتسجيل الأخبار التي تمت معالجتها،
تعتمد على رابط الخبر وعلى عنوان الخبر معاً لمنع إعادة جلب أي خبر مسبقًا نهائيًا.
"""

import sqlite3
import os
import hashlib
import re
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
            title_hash TEXT,
            wp_post_id INTEGER,
            status TEXT,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_url_hash ON processed_news(url_hash)
    """)
    # ترحيل آمن: إضافة عمود title_hash لقاعدة البيانات القديمة (لو الملف موجود من قبل هذا التحديث)
    try:
        cur.execute("ALTER TABLE processed_news ADD COLUMN title_hash TEXT")
    except sqlite3.OperationalError:
        pass  # العمود موجود بالفعل، لا داعي لأي إجراء
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_title_hash ON processed_news(title_hash)
    """)
    conn.commit()
    conn.close()


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _normalize_title(title: str) -> str:
    """
    توحيد شكل العنوان قبل التجزئة، لأن روابط Google News RSS تتغير
    في كل مرة لنفس الخبر (روابط تحويل مؤقتة)، فالاعتماد على الرابط
    فقط لمنع التكرار غير كافٍ، لذلك نعتمد أيضاً على العنوان المطبَّع.
    """
    if not title:
        return ""
    normalized = re.sub(r"\s+", " ", title).strip().lower()
    return normalized


def _hash_title(title: str) -> str:
    return hashlib.sha256(_normalize_title(title).encode("utf-8")).hexdigest()


def is_processed(url: str, title: str = "") -> bool:
    """
    تحقق مما إذا كان الخبر قد تم جلبه أو معالجته مسبقاً،
    عبر الرابط أو عبر العنوان (لأن رابط Google News يتغير لنفس الخبر).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM processed_news WHERE url_hash = ? OR original_url = ?", (_hash_url(url), url))
    if cur.fetchone():
        conn.close()
        return True

    if title:
        cur.execute("SELECT 1 FROM processed_news WHERE title_hash = ?", (_hash_title(title),))
        if cur.fetchone():
            conn.close()
            return True

    conn.close()
    return False


def get_recent_titles(limit: int = 40) -> list[str]:
    """
    يجلب أحدث العناوين المنشورة من قاعدة البيانات.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT title FROM processed_news WHERE title IS NOT NULL AND title != '' AND status LIKE 'published%' ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def mark_processed(url: str, title: str = "", wp_post_id: int = None, status: str = "published"):
    """
    يحفظ رابط الخبر وعنوانه في قاعدة البيانات حتى لا يرجع لهما السكريبت مرة أخرى.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO processed_news (url_hash, original_url, title, title_hash, wp_post_id, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_hash_url(url), url, title, _hash_title(title), wp_post_id, status, datetime.utcnow().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # مسجل بالفعل
    finally:
        conn.close()


init_db()
