"""
db.py
=====
قاعدة بيانات SQLite لمنع تكرار الأخبار وإعادة محاولة الأخبار الفاشلة.

يتم منع التكرار عبر:
- الرابط بعد تنظيفه.
- معرّف خبر كووورة الموجود داخل الرابط (bl...).
- العنوان بعد تنظيف المسافات واسم المصدر فقط.
- حجز الخبر بحالة processing قبل بدء معالجته،
  لمنع تشغيلين متزامنين من معالجة الخبر نفسه.

الأخبار التي تفشل في المعالجة بحالة publish_failed
يمكن إعادة محاولتها لمدة أقصاها 6 ساعات من وقت أول فشل.

كما يتم الاحتفاظ بالأخبار التي تم اكتشافها من كووورة
لكن تعذر فتح صفحة الخبر أو التحقق منها، حتى لا تضيع
ويتم إعادة محاولة التحقق منها خلال فترة الـ6 ساعات.
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


# =========================================================
# تهيئة قاعدة البيانات
# =========================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)

    try:
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

        # -------------------------------------------------
        # ترقية قاعدة البيانات القديمة بإضافة kooora_id
        # -------------------------------------------------

        try:
            cur.execute(
                "ALTER TABLE processed_news ADD COLUMN kooora_id TEXT"
            )
        except sqlite3.OperationalError:
            pass

        # -------------------------------------------------
        # الفهارس
        # -------------------------------------------------

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_url_hash
            ON processed_news(url_hash)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_title_hash
            ON processed_news(title_hash)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_kooora_id
            ON processed_news(kooora_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_created_at
            ON processed_news(status, created_at)
        """)

        conn.commit()

    finally:
        conn.close()


# =========================================================
# تنظيف الرابط
# =========================================================

def _normalize_url(url: str) -> str:
    if not url:
        return ""

    try:
        parts = urlsplit(str(url).strip())

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
        return str(url).strip().rstrip("/")


# =========================================================
# استخراج معرّف خبر كووورة
# =========================================================

def _extract_kooora_id(url: str) -> str:
    """
    يستخرج المعرّف الفريد لخبر كووورة.

    أمثلة:
        .../bltc03f5c344f659766
        .../blt16a7be99bdb47db9
    """

    if not url:
        return ""

    try:
        match = re.search(
            r"/(bl[a-zA-Z0-9]+)(?:[/?#]|$)",
            str(url),
            re.IGNORECASE,
        )

        if match:
            return match.group(1).lower()

    except Exception:
        pass

    return ""


# =========================================================
# تنظيف العنوان
# =========================================================

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


# =========================================================
# Hash
# =========================================================

def _hash_url(url: str) -> str:
    return hashlib.sha256(
        _normalize_url(url).encode("utf-8")
    ).hexdigest()


def _hash_title(title: str) -> str:
    return hashlib.sha256(
        _normalize_title(title).encode("utf-8")
    ).hexdigest()


# =========================================================
# انتهاء فترة إعادة محاولة الفشل
# =========================================================

def _failed_retry_expired(created_at: str) -> bool:
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


# =========================================================
# تحديد هل السجل يعتبر معالجًا
# =========================================================

def _is_matching_record_processed(row) -> bool:
    if not row:
        return False

    status, created_at = row

    # كل الحالات غير publish_failed تعتبر مكتملة
    # أو محجوزة حاليًا.
    if status != "publish_failed":
        return True

    # الفشل القديم انتهت مدة إعادة محاولته.
    if _failed_retry_expired(created_at):
        return True

    # الفشل الحديث يمكن إعادة محاولته.
    return False


# =========================================================
# جلب الأخبار الفاشلة القابلة لإعادة المحاولة
# =========================================================

def get_retryable_failed_news(
    limit: int = 200,
) -> list:

    """
    يعيد الأخبار التي فشلت سابقًا وما زالت داخل
    نافذة إعادة المحاولة البالغة 6 ساعات.

    هذه الدالة مهمة حتى لا يعتمد استرجاع الخبر الفاشل
    على ظهوره مرة أخرى في صفحات كووورة.
    """

    conn = sqlite3.connect(DB_PATH)

    try:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                original_url,
                title,
                created_at
            FROM processed_news
            WHERE status = 'publish_failed'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )

        rows = cur.fetchall()

        results = []

        for row in rows:

            url = row[0]
            title = row[1] or ""
            created_at = row[2]

            if not url:
                continue

            if _failed_retry_expired(
                created_at
            ):
                continue

            results.append(
                {
                    "title": title,
                    "link": url,
                    "failed_at": created_at,
                }
            )

        return results

    finally:
        conn.close()


# =========================================================
# فحص الخبر
# =========================================================

def is_processed(
    url: str,
    title: str = "",
) -> bool:

    conn = sqlite3.connect(DB_PATH)

    try:
        cur = conn.cursor()

        normalized_url = _normalize_url(url)
        url_hash = _hash_url(normalized_url)
        kooora_id = _extract_kooora_id(normalized_url)
        title_hash = _hash_title(title)

        # -------------------------------------------------
        # 1. فحص الرابط
        # -------------------------------------------------

        cur.execute(
            """
            SELECT status, created_at
            FROM processed_news
            WHERE url_hash = ?
            LIMIT 1
            """,
            (url_hash,),
        )

        row = cur.fetchone()

        if row and _is_matching_record_processed(row):
            return True

        # -------------------------------------------------
        # 2. فحص معرّف كووورة
        # -------------------------------------------------

        if kooora_id:
            cur.execute(
                """
                SELECT status, created_at
                FROM processed_news
                WHERE kooora_id = ?
                LIMIT 1
                """,
                (kooora_id,),
            )

            row = cur.fetchone()

            if row and _is_matching_record_processed(row):
                return True

        # -------------------------------------------------
        # 3. فحص العنوان
        # -------------------------------------------------

        if title:
            cur.execute(
                """
                SELECT status, created_at
                FROM processed_news
                WHERE title_hash = ?
                LIMIT 1
                """,
                (title_hash,),
            )

            row = cur.fetchone()

            if row and _is_matching_record_processed(row):
                return True

        return False

    finally:
        conn.close()


# =========================================================
# حجز الخبر
# =========================================================

def claim_news(
    url: str,
    title: str = "",
) -> bool:

    normalized_url = _normalize_url(url)
    url_hash = _hash_url(normalized_url)
    title_hash = _hash_title(title)
    kooora_id = _extract_kooora_id(normalized_url)
    created_at = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    try:
        cur = conn.cursor()

        cur.execute("BEGIN IMMEDIATE")

        # -------------------------------------------------
        # 1. البحث بالرابط
        # -------------------------------------------------

        cur.execute(
            """
            SELECT
                id,
                status,
                created_at
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

            if existing_status != "publish_failed":
                conn.rollback()
                return False

            # فشل حديث → إعادة المحاولة.
            if not _failed_retry_expired(
                existing_created_at
            ):
                cur.execute(
                    """
                    UPDATE processed_news
                    SET
                        original_url = ?,
                        title = ?,
                        title_hash = ?,
                        kooora_id = ?,
                        status = ?,
                        created_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_url,
                        title,
                        title_hash,
                        kooora_id,
                        "processing",
                        existing_created_at,
                        existing_id,
                    ),
                )

                conn.commit()
                return True

            # فشل قديم → دورة جديدة.
            cur.execute(
                """
                UPDATE processed_news
                SET
                    original_url = ?,
                    title = ?,
                    title_hash = ?,
                    kooora_id = ?,
                    status = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    normalized_url,
                    title,
                    title_hash,
                    kooora_id,
                    "processing",
                    created_at,
                    existing_id,
                ),
            )

            conn.commit()
            return True

        # -------------------------------------------------
        # 2. البحث بمعرّف كووورة
        # -------------------------------------------------

        if kooora_id:
            cur.execute(
                """
                SELECT
                    id,
                    status,
                    created_at
                FROM processed_news
                WHERE kooora_id = ?
                LIMIT 1
                """,
                (kooora_id,),
            )

            kooora_existing = cur.fetchone()

            if kooora_existing:
                existing_id = kooora_existing[0]
                existing_status = kooora_existing[1]
                existing_created_at = kooora_existing[2]

                if existing_status != "publish_failed":
                    conn.rollback()
                    return False

                if not _failed_retry_expired(
                    existing_created_at
                ):
                    cur.execute(
                        """
                        UPDATE processed_news
                        SET
                            original_url = ?,
                            title = ?,
                            title_hash = ?,
                            kooora_id = ?,
                            status = ?,
                            created_at = ?
                        WHERE id = ?
                        """,
                        (
                            normalized_url,
                            title,
                            title_hash,
                            kooora_id,
                            "processing",
                            existing_created_at,
                            existing_id,
                        ),
                    )

                    conn.commit()
                    return True

                cur.execute(
                    """
                    UPDATE processed_news
                    SET
                        original_url = ?,
                        title = ?,
                        title_hash = ?,
                        kooora_id = ?,
                        status = ?,
                        created_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_url,
                        title,
                        title_hash,
                        kooora_id,
                        "processing",
                        created_at,
                        existing_id,
                    ),
                )

                conn.commit()
                return True

        # -------------------------------------------------
        # 3. البحث بالعنوان
        # -------------------------------------------------

        if title:
            cur.execute(
                """
                SELECT
                    id,
                    status,
                    created_at
                FROM processed_news
                WHERE title_hash = ?
                LIMIT 1
                """,
                (title_hash,),
            )

            title_existing = cur.fetchone()

            if title_existing:
                existing_id = title_existing[0]
                existing_status = title_existing[1]
                existing_created_at = title_existing[2]

                if existing_status != "publish_failed":
                    conn.rollback()
                    return False

                if not _failed_retry_expired(
                    existing_created_at
                ):
                    cur.execute(
                        """
                        UPDATE processed_news
                        SET
                            original_url = ?,
                            title = ?,
                            title_hash = ?,
                            kooora_id = ?,
                            status = ?,
                            created_at = ?
                        WHERE id = ?
                        """,
                        (
                            normalized_url,
                            title,
                            title_hash,
                            kooora_id,
                            "processing",
                            existing_created_at,
                            existing_id,
                        ),
                    )

                    conn.commit()
                    return True

                cur.execute(
                    """
                    UPDATE processed_news
                    SET
                        original_url = ?,
                        title = ?,
                        title_hash = ?,
                        kooora_id = ?,
                        status = ?,
                        created_at = ?
                    WHERE id = ?
                    """,
                    (
                        normalized_url,
                        title,
                        title_hash,
                        kooora_id,
                        "processing",
                        created_at,
                        existing_id,
                    ),
                )

                conn.commit()
                return True

        # -------------------------------------------------
        # 4. خبر جديد
        # -------------------------------------------------

        cur.execute(
            """
            INSERT INTO processed_news (
                url_hash,
                original_url,
                title,
                title_hash,
                kooora_id,
                wp_post_id,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                url_hash,
                normalized_url,
                title,
                title_hash,
                kooora_id,
                None,
                "processing",
                created_at,
            ),
        )

        conn.commit()
        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        return False

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# تحديث الرابط النهائي بعد استخراج المقال
# =========================================================

def update_claimed_url(
    original_url: str,
    final_url: str,
    title: str = "",
) -> bool:

    original_normalized = _normalize_url(
        original_url
    )

    final_normalized = _normalize_url(
        final_url
    )

    if not final_normalized:
        return True

    original_hash = _hash_url(
        original_normalized
    )

    final_hash = _hash_url(
        final_normalized
    )

    kooora_id = _extract_kooora_id(
        final_normalized
    )

    title_hash = _hash_title(title)

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    try:
        cur = conn.cursor()

        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            SELECT id
            FROM processed_news
            WHERE url_hash = ?
            AND status = 'processing'
            LIMIT 1
            """,
            (original_hash,),
        )

        current = cur.fetchone()

        if not current:
            conn.rollback()
            return False

        current_id = current[0]

        if final_hash != original_hash:
            cur.execute(
                """
                SELECT id, status
                FROM processed_news
                WHERE url_hash = ?
                AND id != ?
                LIMIT 1
                """,
                (
                    final_hash,
                    current_id,
                ),
            )

            duplicate_url = cur.fetchone()

            if duplicate_url:
                conn.rollback()
                return False

        if kooora_id:
            cur.execute(
                """
                SELECT id, status
                FROM processed_news
                WHERE kooora_id = ?
                AND id != ?
                LIMIT 1
                """,
                (
                    kooora_id,
                    current_id,
                ),
            )

            duplicate_kooora = cur.fetchone()

            if duplicate_kooora:
                conn.rollback()
                return False

        cur.execute(
            """
            UPDATE processed_news
            SET
                url_hash = ?,
                original_url = ?,
                title = ?,
                title_hash = ?,
                kooora_id = ?
            WHERE id = ?
            """,
            (
                final_hash,
                final_normalized,
                title,
                title_hash,
                kooora_id,
                current_id,
            ),
        )

        conn.commit()
        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        return False

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# العناوين المنشورة مؤخرًا
# =========================================================

def get_recent_titles(
    limit: int = 40,
) -> list[str]:

    conn = sqlite3.connect(DB_PATH)

    try:
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

        return [
            row[0]
            for row in rows
        ]

    finally:
        conn.close()


# =========================================================
# تسجيل النتيجة النهائية
# =========================================================

def mark_processed(
    url: str,
    title: str = "",
    wp_post_id: int = None,
    status: str = "published",
):
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    try:
        cur = conn.cursor()

        normalized_url = _normalize_url(url)
        url_hash = _hash_url(normalized_url)
        title_hash = _hash_title(title)
        kooora_id = _extract_kooora_id(
            normalized_url
        )

        created_at = datetime.now(
            timezone.utc
        ).isoformat()

        cur.execute(
            """
            SELECT
                id,
                status,
                created_at
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

            # عند استمرار الفشل، نحافظ على وقت أول فشل.
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
                    kooora_id = ?,
                    wp_post_id = ?,
                    status = ?,
                    created_at = ?
                WHERE id = ?
                """,
                (
                    normalized_url,
                    title,
                    title_hash,
                    kooora_id,
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
                    kooora_id,
                    wp_post_id,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    url_hash,
                    normalized_url,
                    title,
                    title_hash,
                    kooora_id,
                    wp_post_id,
                    status,
                    created_at,
                ),
            )

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()

    finally:
        conn.close()


# =========================================================
# أسماء توافقية قديمة
# =========================================================

mark_as_processed = mark_processed
add_processed_news = mark_processed
save_article = mark_processed


# =========================================================
# تشغيل التهيئة
# =========================================================

init_db()
