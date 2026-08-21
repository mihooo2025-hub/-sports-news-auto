"""
db.py
=====
قاعدة بيانات SQLite لمنع تكرار الأخبار.

يتم منع التكرار عبر:
- الرابط بعد تنظيف بسيط.
- العنوان بعد تنظيف المسافات واسم المصدر فقط.

لا يتم إجراء تطبيع قوي للعناوين حتى لا يتم اعتبار
أخبار مختلفة متشابهة على أنها خبر واحد.

الأخبار التي تفشل في المعالجة بحالة publish_failed
يمكن إعادة محاولتها لمدة أقصاها 6 ساعات من وقت أول فشل.
بعد مرور 6 ساعات يتم تجاهل الخبر نهائيًا.
"""

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, urlunsplit


DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "news.db",
)

FAILED_RETRY_HOURS = 6


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


def _failed_retry_expired(created_at: str) -> bool:
    """
    التحقق من انتهاء مدة إعادة محاولة الخبر الفاشل.

    إذا مر أكثر من 6 ساعات على وقت أول فشل،
    يتم تجاهل الخبر نهائيًا.
    """

    if not created_at:
        return False

    try:
        failed_at = datetime.fromisoformat(
            created_at
        )

        if failed_at.tzinfo is None:
            failed_at = failed_at.replace(
                tzinfo=timezone.utc
            )

        now = datetime.now(timezone.utc)

        return (
            now - failed_at
            >= timedelta(hours=FAILED_RETRY_HOURS)
        )

    except Exception:
        return False


def _is_matching_record_processed(row) -> bool:
    """
    تحديد ما إذا كان سجل الخبر يجب اعتباره مكتملًا.

    الحالات:
    - أي حالة ليست publish_failed = الخبر تمت معالجته بالفعل.
    - publish_failed خلال آخر 6 ساعات = يسمح بإعادة المحاولة.
    - publish_failed بعد 6 ساعات = يتم تجاهله نهائيًا.
    """

    if not row:
        return False

    status, created_at = row

    if status != "publish_failed":
        return True

    if _failed_retry_expired(created_at):
        return True

    return False


def is_processed(
    url: str,
    title: str = "",
) -> bool:
    """
    التحقق من وجود الخبر سابقًا.

    الأخبار التي فشلت في المعالجة:
    - يعاد فحصها ومحاولة معالجتها خلال أول 6 ساعات.
    - بعد مرور 6 ساعات على أول فشل يتم تجاهلها نهائيًا.
    """

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT status, created_at
        FROM processed_news
        WHERE url_hash = ?
        LIMIT 1
        """,
        (_hash_url(url),),
    )

    row = cur.fetchone()

    if row and _is_matching_record_processed(row):
        conn.close()
        return True

    if title:
        cur.execute(
            """
            SELECT status, created_at
            FROM processed_news
            WHERE title_hash = ?
            LIMIT 1
            """,
            (_hash_title(title),),
        )

        row = cur.fetchone()

        if row and _is_matching_record_processed(row):
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
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    normalized_url = _normalize_url(url)
    url_hash = _hash_url(normalized_url)
    title_hash = _hash_title(title)
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        cur.execute(
            """
            SELECT id, status, created_at
            FROM processed_news
            WHERE url_hash = ?
            LIMIT 1
            """,
            (url_hash,),
        )

        existing = cur.fetchone()

        if existing:
            existing_id = existing[0]
            existing_status = existing[1]
            existing_created_at = existing[2]

            # عند إعادة تسجيل نفس الخبر كـ publish_failed،
            # نحافظ على وقت أول فشل بدل تجديد مدة الـ6 ساعات.
            if (
                status == "publish_failed"
                and existing_status == "publish_failed"
                and existing_created_at
            ):
                created_at = existing_created_at

            cur.execute(
                """
                UPDATE processed_news
                SET
                    original_url = ?,
                    title = ?,
                    title_hash = ?,
                    wp_post_id = ?,
                    status = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    normalized_url,
                    title,
                    title_hash,
                    wp_post_id,
                    status,
                    created_at,
                    existing_id,
                ),
            )

        else:
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
                    url_hash,
                    normalized_url,
                    title,
                    title_hash,
                    wp_post_id,
                    status,
                    created_at,
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
