"""
db.py
=====
قاعدة بيانات SQLite لمنع تكرار الأخبار.

يتم منع التكرار عبر:
- الرابط بعد تنظيف بسيط.
- العنوان بعد تنظيف المسافات واسم المصدر فقط.

لا يتم إجراء تطبيع قوي للعناوين حتى لا يتم اعتبار
أخبار مختلفة متشابهة على أنها خبر واحد.

الأخبار التي تفشل في النشر بحالة publish_failed
يمكن إعادة محاولتها.
"""

import hashlib
import os
import re
import sqlite3
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit


DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "news.db",
)


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
        CREATE INDEX IF NOT EXISTS idx_url_hash
        ON processed_news(url_hash)
    """)

    try:
        cur.execute(
            "ALTER TABLE processed_news ADD COLUMN title_hash TEXT"
        )
    except sqlite3.OperationalError:
        pass

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_title_hash
        ON processed_news(title_hash)
    """)

    conn.commit()
    conn.close()


def _normalize_url(url: str) -> str:
    """
    تنظيف بسيط للرابط فقط.

    يتم تجاهل:
    - www
    - query parameters
    - fragments
    - slash في نهاية الرابط

    ولا يتم تغيير مسار الخبر نفسه.
    """

    if not url:
        return ""

    try:
        parts = urlsplit(url.strip())

        host = (parts.hostname or "").lower()

        if host.startswith("www."):
            host = host[4:]

        path = parts.path.rstrip("/")

        return urlunsplit(
            (
                "https",
                host,
                path,
                "",
                "",
            )
        )

    except Exception:
        return url.strip().rstrip("/")


def _normalize_title(title: str) -> str:
    """
    تنظيف محافظ جدًا للعنوان.

    لا نغير الحروف العربية ولا نحذف علامات الترقيم.
    فقط:
    - إزالة اسم كووورة من نهاية العنوان.
    - توحيد المسافات.
    - تحويل الأحرف الإنجليزية إلى lowercase.
    """

    if not title:
        return ""

    title = str(title).strip()

    for suffix in (
        " - كووورة",
        " - كوووره",
        " - Kooora",
        " - kooora",
    ):
        if title.endswith(suffix):
            title = title[:-len(suffix)].strip()

    title = re.sub(
        r"\s+",
        " ",
        title,
    )

    return title.lower()


def _hash_url(url: str) -> str:
    return hashlib.sha256(
        _normalize_url(url).encode("utf-8")
    ).hexdigest()


def _hash_title(title: str) -> str:
    return hashlib.sha256(
        _normalize_title(title).encode("utf-8")
    ).hexdigest()


def is_processed(
    url: str,
    title: str = "",
) -> bool:
    """
    التحقق من وجود الخبر سابقًا.

    يمنع التكرار فقط إذا:
    - الرابط نفسه بعد تنظيف بسيط.
    أو
    - العنوان نفسه بعد تنظيف بسيط.

    publish_failed لا يمنع إعادة المحاولة.
    """

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT 1
        FROM processed_news
        WHERE url_hash = ?
        AND status != 'publish_failed'
        LIMIT 1
        """,
        (_hash_url(url),),
    )

    if cur.fetchone():
        conn.close()
        return True

    if title:
        cur.execute(
            """
            SELECT 1
            FROM processed_news
            WHERE title_hash = ?
            AND status != 'publish_failed'
            LIMIT 1
            """,
            (_hash_title(title),),
        )

        if cur.fetchone():
            conn.close()
            return True

    conn.close()
    return False


def get_recent_titles(
    limit: int = 40,
) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT title
        FROM processed_news
        WHERE title IS NOT NULL
        AND title != ''
        AND status LIKE 'published%'
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    conn.close()

    return [row[0] for row in rows]


def mark_processed(
    url: str,
    title: str = "",
    wp_post_id: int = None,
    status: str = "published",
):
    """
    تسجيل الخبر في قاعدة البيانات.
    """

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    normalized_url = _normalize_url(url)

    try:
        cur.execute(
            """
            INSERT INTO processed_news (
                url_hash,
                original_url,
                title,
                title_hash,
                wp_post_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _hash_url(normalized_url),
                normalized_url,
                title,
                _hash_title(title),
                wp_post_id,
                status,
                datetime.utcnow().isoformat(),
            ),
        )

        conn.commit()

    except sqlite3.IntegrityError:
        pass

    finally:
        conn.close()


mark_as_processed = mark_processed
add_processed_news = mark_processed
save_article = mark_processed


init_db()
