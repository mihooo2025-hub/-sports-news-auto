"""
main.py
=======
إدارة دورة جلب أخبار كووورة ومعالجتها ونشرها كمسودات في WordPress.

آلية العمل:
1. تثبيت وقت بداية الدورة.
2. البحث عن جميع أخبار كووورة المنشورة خلال آخر 6 ساعات.
3. جلب المقال كاملًا مع الصورة البارزة.
4. تجاهل الخبر إذا لم تتوفر صورة بارزة قابلة للتحميل.
5. منع تكرار الأخبار التي تمت معالجتها سابقًا.
6. إعادة محاولة الأخبار التي فشلت معالجتها في الدورات التالية.
7. إعادة صياغة الخبر عبر Gemini.
8. الانتظار 10 ثوانٍ بين عمليات إعادة الصياغة.
9. إنشاء مسودة في WordPress.
10. الانتظار 3 ثوانٍ بعد النشر.
11. إرسال تقرير إلى Telegram بعد انتهاء الدورة.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone

import db
from config import CONFIG
from kooora_scraper import KoooraScraper
from content_ai import (
    process_article,
    is_gemini_quota_exhausted,
)
from wordpress_publisher import (
    publish_post,
    test_authentication,
)
from telegram_reporter import (
    send_cycle_report,
    send_error_alert,
)


# =========================================================
# الإعدادات
# =========================================================

LOOKBACK_HOURS = 6
AI_DELAY_SECONDS = 10
PUBLISH_DELAY_SECONDS = 3


# =========================================================
# قاعدة البيانات
# =========================================================

def mark_db_record(
    url: str,
    title: str,
    status: str,
):
    """
    تسجيل حالة الخبر في قاعدة البيانات.
    """

    db.mark_processed(
        url=url,
        title=title,
        status=status,
    )


def is_already_processed(
    url: str,
    title: str,
) -> bool:
    """
    التحقق مما إذا كان الخبر قد تمت معالجته سابقًا.

    الأخبار المنشورة أو المسجلة كمسودة لا تعالج مرة أخرى.

    الأخبار الفاشلة لا تعتبر مكتملة لكي يمكن إعادة
    محاولة معالجتها في الدورة التالية.
    """

    try:
        return db.is_processed(
            url=url,
            title=title,
        )

    except TypeError:
        try:
            return db.is_processed(url)

        except Exception as exc:
            print(
                f"⚠️ تعذر التحقق من حالة التكرار: {exc}"
            )

            return False

    except Exception as exc:
        print(
            f"⚠️ تعذر التحقق من حالة التكرار: {exc}"
        )

        return False


# =========================================================
# التصنيفات
# =========================================================

def map_category_names_to_ids(
    category_names: list,
) -> list:
    """
    تنظيف التصنيفات القادمة من الذكاء الاصطناعي.

    لا يتم إنشاء أي تصنيف جديد هنا.
    WordPress Publisher مسؤول عن مطابقة الأسماء
    مع التصنيفات الموجودة فعليًا في الموقع.

    يتم منع:
    - أهم الاخبار
    - مقالات وتحليلات

    ويتم الاحتفاظ بالتصنيف الرئيسي الإجباري إذا كان
    موجودًا في CONFIG.
    """

    if not isinstance(category_names, list):
        category_names = []

    configured_categories = CONFIG.get(
        "categories",
        [],
    )

    if not isinstance(configured_categories, list):
        configured_categories = []

    excluded_categories = {
        "اهم الاخبار",
        "أهم الأخبار",
        "أهم الاخبار",
        "مقالات وتحليلات",
    }

    cleaned_categories = []

    for category in category_names:
        if not isinstance(category, str):
            continue

        category = category.strip()

        if not category:
            continue

        if category in excluded_categories:
            continue

        if category not in cleaned_categories:
            cleaned_categories.append(category)

    # الاحتفاظ فقط بالتصنيفات المسموح بها في الإعدادات
    if configured_categories:
        cleaned_categories = [
            category
            for category in cleaned_categories
            if category in configured_categories
        ]

    # الحد الأقصى 3 تصنيفات من الذكاء الاصطناعي
    cleaned_categories = cleaned_categories[:3]

    # -----------------------------------------------------
    # التصنيف الرئيسي الإجباري باللغة الإنجليزية
    # -----------------------------------------------------

    main_category = CONFIG.get(
        "main_category",
        ""
    )

    if not main_category:
        main_category = CONFIG.get(
            "required_main_category",
            ""
        )

    if (
        isinstance(main_category, str)
        and main_category.strip()
    ):
        main_category = main_category.strip()

        if (
            main_category in configured_categories
            and main_category not in cleaned_categories
        ):
            cleaned_categories.append(
                main_category
            )

    return cleaned_categories


# =========================================================
# معالجة خبر واحد
# =========================================================

def process_candidate(
    scraper: KoooraScraper,
    candidate,
    position: int,
    total: int,
):
    """
    معالجة خبر واحد بالكامل.

    Returns:
        tuple:
            (
                status,
                published_item_or_none,
                details
            )
    """

    source_url = candidate.url
    source_title = candidate.title

    print(
        "\n"
        + "=" * 70
    )

    print(
        f"[{position}/{total}] "
        f"جاري معالجة الخبر"
    )

    print(
        f"العنوان: {source_title}"
    )

    print(
        f"الرابط: {source_url}"
    )

    print(
        f"وقت النشر: "
        f"{candidate.published_at.isoformat()}"
    )

    # =====================================================
    # منع التكرار
    # =====================================================

    if is_already_processed(
        source_url,
        source_title,
    ):
        print(
            "⏭️ تم تجاوز الخبر لأنه تمت معالجته سابقًا."
        )

        return (
            "skipped",
            None,
            "duplicate",
        )

    # =====================================================
    # جلب المقال كاملًا
    # =====================================================

    try:
        article = scraper.fetch_article(
            candidate
        )

    except Exception as exc:
        print(
            f"⚠️ حدث خطأ أثناء جلب المقال: {exc}"
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "article_fetch_failed",
        )

    if article is None:
        print(
            "⚠️ تعذر استخراج المقال."
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "article_fetch_failed",
        )

    # =====================================================
    # التحقق من نص المقال
    # =====================================================

    raw_content = (
        article.text or ""
    ).strip()

    if not raw_content:
        print(
            "⚠️ المقال لا يحتوي على نص صالح."
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "empty_content",
        )

    # =====================================================
    # التحقق من الصورة البارزة
    # =====================================================

    image_url = (
        article.image_url or ""
    ).strip()

    image_bytes = article.image_bytes
    image_mime = article.image_mime

    if (
        not image_url
        or not image_bytes
        or not image_mime
    ):
        print(
            "⏭️ تم تجاهل الخبر لأنه لا يحتوي على "
            "صورة بارزة قابلة للاستخدام."
        )

        mark_db_record(
            source_url,
            source_title,
            "skipped_no_image",
        )

        return (
            "skipped",
            None,
            "no_image",
        )

    print(
        f"📝 تم استخراج المقال "
        f"({len(raw_content.split())} كلمة تقريبًا)."
    )

    print(
        f"🖼️ الصورة البارزة: {image_url}"
    )

    # =====================================================
    # Gemini
    # =====================================================

    try:
        ai_result = process_article(
            raw_content,
            source_title,
            "",
        )

    except Exception as exc:
        print(
            f"⚠️ حدث خطأ أثناء معالجة Gemini: {exc}"
        )

        if is_gemini_quota_exhausted():
            return (
                "quota_exhausted",
                None,
                "gemini_quota_exhausted",
            )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "ai_failed",
        )

    # =====================================================
    # نفاد الحصة
    # =====================================================

    if is_gemini_quota_exhausted():
        print(
            "⛔ لا يوجد مفتاح Gemini متاح حاليًا."
        )

        print(
            "⏹️ سيتم إيقاف الدورة دون تسجيل الأخبار "
            "المتبقية كفاشلة."
        )

        return (
            "quota_exhausted",
            None,
            "gemini_quota_exhausted",
        )

    # =====================================================
    # فشل نتيجة الذكاء الاصطناعي
    # =====================================================

    if not ai_result:
        print(
            "⚠️ لم يرجع Gemini نتيجة صالحة."
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "ai_failed",
        )

    rewritten_title = str(
        ai_result.get(
            "title",
            ""
        ) or ""
    ).strip()

    rewritten_content = str(
        ai_result.get(
            "rewritten_content",
            ""
        ) or ""
    ).strip()

    category_names = ai_result.get(
        "categories",
        [],
    )

    # =====================================================
    # التحقق من نتيجة Gemini
    # =====================================================

    if (
        not rewritten_title
        or not rewritten_content
    ):
        print(
            "⚠️ نتيجة Gemini ناقصة."
        )

        print(
            f"العنوان موجود: {bool(rewritten_title)}"
        )

        print(
            f"المحتوى موجود: {bool(rewritten_content)}"
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "ai_incomplete",
        )

    # =====================================================
    # تأخير 10 ثوانٍ بين إعادة الصياغة
    # =====================================================

    print(
        f"⏳ انتظار {AI_DELAY_SECONDS} ثوانٍ "
        "قبل متابعة العملية التالية..."
    )

    time.sleep(
        AI_DELAY_SECONDS
    )

    # =====================================================
    # تجهيز التصنيفات
    # =====================================================

    categories_to_publish = (
        map_category_names_to_ids(
            category_names
        )
    )

    print(
        f"📂 التصنيفات المختارة: "
        f"{categories_to_publish or 'بدون تصنيف إضافي'}"
    )

    # =====================================================
    # WordPress
    # =====================================================

    try:
        site_url = publish_post(
            title=rewritten_title,
            content=rewritten_content,
            categories=categories_to_publish,

            # نحافظ على image_url للتوافق
            # مع النسخة الحالية من WordPress Publisher
            image_url=image_url,
        )

    except Exception as exc:
        print(
            f"❌ حدث خطأ أثناء إنشاء مسودة WordPress: {exc}"
        )

        mark_db_record(
            source_url,
            source_title,
            "publish_failed",
        )

        return (
            "failed",
            None,
            "wordpress_failed",
        )

    # =====================================================
    # تأخير 3 ثوانٍ بعد النشر
    # =====================================================

    print(
        f"⏳ انتظار {PUBLISH_DELAY_SECONDS} ثوانٍ "
        "بعد عملية WordPress..."
    )

    time.sleep(
        PUBLISH_DELAY_SECONDS
    )

    # =====================================================
    # نجاح إنشاء المسودة
    # =====================================================

    if site_url:
        print(
            f"✅ تم إنشاء المسودة بنجاح: {site_url}"
        )

        mark_db_record(
            source_url,
            source_title,
            "published",
        )

        return (
            "published",
            {
                "title": rewritten_title,
                "source_url": source_url,
                "site_url": site_url,
            },
            "success",
        )

    # =====================================================
    # فشل WordPress بدون Exception
    # =====================================================

    print(
        "❌ لم يرجع WordPress رابطًا صالحًا للمسودة."
    )

    mark_db_record(
        source_url,
        source_title,
        "publish_failed",
    )

    return (
        "failed",
        None,
        "wordpress_failed",
    )


# =========================================================
# الدورة الرئيسية
# =========================================================

def run_pipeline():
    """
    تشغيل دورة الأخبار كاملة.
    """

    # =====================================================
    # تثبيت وقت بداية الدورة
    # =====================================================

    cycle_start = datetime.now(
        timezone.utc
    )

    cutoff = (
        cycle_start
        - timedelta(
            hours=LOOKBACK_HOURS
        )
    )

    print(
        "\n"
        + "=" * 70
    )

    print(
        "🚀 بدء دورة أخبار كووورة"
    )

    print(
        f"وقت بداية الدورة: "
        f"{cycle_start.isoformat()}"
    )

    print(
        f"فحص الأخبار منذ: "
        f"{cutoff.isoformat()}"
    )

    print(
        f"نافذة البحث: "
        f"آخر {LOOKBACK_HOURS} ساعات"
    )

    print(
        "=" * 70
    )

    # =====================================================
    # قاعدة البيانات
    # =====================================================

    db.init_db()

    # =====================================================
    # WordPress
    # =====================================================

    if not test_authentication():
        error_message = (
            "❌ تعذر الوصول إلى WordPress REST API. "
            "تحقق من بيانات الموقع واسم المستخدم "
            "وكلمة مرور التطبيق."
        )

        print(
            error_message
        )

        send_error_alert(
            error_message
        )

        return

    # =====================================================
    # إنشاء Scraper
    # =====================================================

    scraper = KoooraScraper()

    # =====================================================
    # اكتشاف الأخبار
    # =====================================================

    try:
        candidates = scraper.discover(
            cutoff=cutoff,
            now=cycle_start,
        )

    except Exception as exc:
        error_message = (
            f"❌ حدث خطأ أثناء اكتشاف أخبار كووورة: {exc}"
        )

        print(
            error_message
        )

        send_error_alert(
            error_message
        )

        return

    checked_count = len(
        candidates
    )

    print(
        f"\n🔍 تم اكتشاف {checked_count} خبر "
        f"خلال آخر {LOOKBACK_HOURS} ساعات."
    )

    # =====================================================
    # لا توجد أخبار
    # =====================================================

    if not candidates:
        print(
            "ℹ️ لا توجد أخبار جديدة ضمن نافذة البحث."
        )

        send_cycle_report(
            [],
            0,
            0,
        )

        return

    # =====================================================
    # الإحصائيات
    # =====================================================

    published_items = []

    published_count = 0
    failed_count = 0
    skipped_count = 0

    failed_extraction = 0
    failed_ai = 0
    failed_publish = 0

    no_image_count = 0
    duplicate_count = 0

    quota_exhausted = False

    # =====================================================
    # معالجة الأخبار
    # =====================================================

    for index, candidate in enumerate(
        candidates,
        start=1,
    ):
        status, published_item, details = (
            process_candidate(
                scraper=scraper,
                candidate=candidate,
                position=index,
                total=checked_count,
            )
        )

        # -------------------------------------------------
        # نفاد Gemini
        # -------------------------------------------------

        if status == "quota_exhausted":
            quota_exhausted = True

            print(
                "\n⏹️ تم إيقاف معالجة الأخبار بسبب "
                "نفاد جميع مفاتيح Gemini المتاحة."
            )

            break

        # -------------------------------------------------
        # نجاح
        # -------------------------------------------------

        if status == "published":
            published_count += 1

            if published_item:
                published_items.append(
                    published_item
                )

            continue

        # -------------------------------------------------
        # تجاوز
        # -------------------------------------------------

        if status == "skipped":
            skipped_count += 1

            if details == "no_image":
                no_image_count += 1

            elif details == "duplicate":
                duplicate_count += 1

            continue

        # -------------------------------------------------
        # فشل
        # -------------------------------------------------

        if status == "failed":
            failed_count += 1

            if details in {
                "article_fetch_failed",
                "empty_content",
            }:
                failed_extraction += 1

            elif details in {
                "ai_failed",
                "ai_incomplete",
            }:
                failed_ai += 1

            elif details == "wordpress_failed":
                failed_publish += 1

    # =====================================================
    # تقرير النتائج
    # =====================================================

    print(
        "\n"
        + "=" * 70
    )

    print(
        "📊 تقرير دورة الأخبار"
    )

    print(
        f"🔍 تم فحص: {checked_count}"
    )

    print(
        f"📝 تم إنشاء مسودات: {published_count}"
    )

    print(
        f"❌ فشل المعالجة: {failed_count}"
    )

    print(
        f"⏭️ تم تجاوزها: {skipped_count}"
    )

    print(
        f"🖼️ بدون صورة بارزة: {no_image_count}"
    )

    print(
        f"🔁 أخبار مكررة: {duplicate_count}"
    )

    print(
        f"⚠️ فشل استخراج المقال: {failed_extraction}"
    )

    print(
        f"🤖 فشل Gemini: {failed_ai}"
    )

    print(
        f"❌ فشل WordPress: {failed_publish}"
    )

    if quota_exhausted:
        print(
            "⛔ توقفت الدورة بسبب عدم توفر "
            "أي مفتاح Gemini صالح."
        )

    print(
        "=" * 70
    )

    # =====================================================
    # إرسال تقرير Telegram
    # =====================================================

    try:
        send_cycle_report(
            published_items,
            checked_count,
            failed_count + skipped_count,
        )

    except Exception as exc:
        print(
            f"⚠️ تعذر إرسال تقرير Telegram: {exc}"
        )

    print(
        "\n🎉 اكتملت الدورة."
    )


# =========================================================
# نقطة التشغيل
# =========================================================

if __name__ == "__main__":
    try:
        run_pipeline()

    except KeyboardInterrupt:
        print(
            "\n⏹️ تم إيقاف الدورة يدويًا."
        )

        sys.exit(0)

    except Exception as exc:
        error_message = (
            f"💥 حدث خطأ غير متوقع أثناء تنفيذ الدورة: {exc}"
        )

        print(
            error_message
        )

        try:
            send_error_alert(
                error_message
            )

        except Exception as telegram_exc:
            print(
                f"⚠️ تعذر إرسال تنبيه الخطأ إلى Telegram: "
                f"{telegram_exc}"
            )

        sys.exit(1)
