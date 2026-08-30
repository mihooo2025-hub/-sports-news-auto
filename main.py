"""
main.py
=======
إدارة دورة جلب الأخبار ومعالجتها ونشرها في WordPress.

الأخبار التي تفشل أثناء:
- استخراج المقال
- معالجة الذكاء الاصطناعي
- النشر في WordPress

تسجل كـ publish_failed وتتم إعادة محاولتها في الدورات التالية.

تتم إعادة محاولة الخبر الفاشل لمدة أقصاها 6 ساعات
من وقت أول فشل، وبعدها يتم تجاهله.
"""

import sys
import time
from datetime import datetime, timezone

import db
from config import CONFIG
from rss_fetcher import (
    fetch_prioritized_news,
    get_filtered_title_items,
)
from article_extractor import extract_article
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


def map_category_names_to_ids(
    category_names: list,
) -> list:
    configured = CONFIG.get(
        "categories",
        [],
    )

    if not isinstance(
        configured,
        list,
    ):
        return category_names

    return [
        category
        for category in category_names
        if category in configured
    ]


def run_pipeline():
    # ======================================================
    # تثبيت وقت بداية الدورة قبل أي خطوة أخرى
    # ======================================================

    cycle_start = datetime.now(
        timezone.utc
    )

    print(
        "🚀 بدء دورة جلب ونشر الأخبار الرياضية..."
    )

    print(
        f"🕐 وقت بداية الدورة: "
        f"{cycle_start.isoformat()}"
    )

    db.init_db()

    if not test_authentication():
        print(
            "❌ تعذر الوصول إلى WordPress — "
            "تم إيقاف الدورة."
        )

        send_error_alert(
            "❌ تعذر الوصول إلى WordPress REST API. "
            "تحقق من بيانات الدخول أو HTTP 403 / Bot Verification."
        )

        return

    # ======================================================
    # جلب الأخبار ضمن نافذة 3 ساعات من بداية الدورة
    # ======================================================

    news_items = fetch_prioritized_news(
        cycle_start=cycle_start
    )

    # ======================================================
    # الأخبار التي تم استبعادها بسبب عنوانها
    # ======================================================

    filtered_title_items = (
        get_filtered_title_items()
    )

    checked_count = len(
        news_items
    )

    if not news_items:
        print(
            "ℹ️ لم يتم العثور على أخبار جديدة ضمن آخر 3 ساعات "
            "من بداية الدورة."
        )

        send_cycle_report(
            [],
            0,
            0,
            filtered_title_items,
        )

        return

    print(
        f"🔍 تم العثور على {checked_count} "
        "خبر ضمن نافذة آخر 3 ساعات."
    )

    published_items = []
    skipped_count = 0

    failed_extraction = 0
    failed_ai = 0
    failed_publish = 0
    blocked_domain = 0

    quota_exhausted = False

    for idx, item in enumerate(
        news_items,
        start=1,
    ):

        source_title = item.get(
            "title",
            "",
        )

        source_link = item.get(
            "link",
            "",
        )

        matched_keyword = item.get(
            "matched_keyword",
            "",
        )

        print(
            f"\n[{idx}/{checked_count}] "
            f"جاري معالجة الخبر: {source_title}"
        )

        # ==================================================
        # حجز الخبر لمنع التكرار
        # ==================================================

        try:
            claimed = db.claim_news(
                source_link,
                source_title,
            )

        except Exception as e:
            print(
                f"⚠️ تعذر حجز الخبر في قاعدة البيانات: {e}"
            )

            print(
                "⏭️ سيتم تجاوز الخبر حفاظًا على منع التكرار."
            )

            skipped_count += 1
            continue

        if not claimed:
            print(
                "⏭️ تم تجاوز الخبر لأنه موجود أو "
                "محجوز مسبقًا في قاعدة البيانات."
            )

            skipped_count += 1
            continue

        print(
            "🔒 تم حجز الخبر للمعالجة."
        )

        # ==================================================
        # استخراج المقال
        # ==================================================

        try:
            extracted_data = extract_article(
                source_link
            )

        except Exception as e:
            print(
                f"⚠️ حدث خطأ أثناء استخراج المقال: {e}"
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_extraction += 1
            skipped_count += 1
            continue

        # ==================================================
        # نطاق ممنوع
        # ==================================================

        if extracted_data.get(
            "blocked"
        ):
            print(
                "🚫 تجاوز الخبر لأنه ينتمي إلى نطاق ممنوع."
            )

            mark_db_record(
                source_link,
                source_title,
                "skipped_blocked_domain",
            )

            blocked_domain += 1
            skipped_count += 1
            continue

        raw_content = extracted_data.get(
            "text",
            "",
        )

        image_url = (
            extracted_data.get(
                "image_url"
            )
            or extracted_data.get(
                "image"
            )
            or item.get(
                "image_url"
            )
        )

        resolved_url = (
            extracted_data.get(
                "resolved_url"
            )
            or source_link
        )

        # ==================================================
        # فشل استخراج المحتوى
        # ==================================================

        if (
            not extracted_data.get(
                "success"
            )
            or not raw_content
        ):
            print(
                "⚠️ تعذر جلب محتوى المقال."
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_extraction += 1
            skipped_count += 1
            continue

        # ==================================================
        # Gemini
        # ==================================================

        try:
            ai_result = process_article(
                raw_content,
                source_title,
                matched_keyword,
            )

        except Exception as e:
            print(
                f"⚠️ حدث خطأ أثناء معالجة Gemini: {e}"
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1
            continue

        # ==================================================
        # نفاد جميع حصص Gemini
        # ==================================================

        if is_gemini_quota_exhausted():
            print(
                "⛔ لم يعد هناك نموذج Gemini متاح "
                "للمعالجة في هذه الدورة."
            )

            print(
                "⏹️ سيتم إيقاف الدورة الآن."
            )

            print(
                "ℹ️ الخبر الحالي والأخبار المتبقية "
                "لن يتم تسجيلها كفشل."
            )

            quota_exhausted = True
            break

        time.sleep(5)

        # ==================================================
        # فشل Gemini عادي
        # ==================================================

        if not ai_result:
            print(
                "⚠️ فشلت معالجة المقال بواسطة الذكاء الاصطناعي."
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1
            continue

        rewritten_title = ai_result.get(
            "title",
            "",
        )

        rewritten_content = ai_result.get(
            "rewritten_content",
            "",
        )

        category_names = ai_result.get(
            "categories",
            [],
        )

        # ==================================================
        # نتيجة Gemini ناقصة
        # ==================================================

        if (
            not rewritten_title
            or not rewritten_content
        ):
            print(
                "⚠️ نتيجة الذكاء الاصطناعي ناقصة."
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1
            continue

        categories_to_publish = (
            map_category_names_to_ids(
                category_names
            )
        )

        # ==================================================
        # WordPress
        # ==================================================

        try:
            site_url = publish_post(
                title=rewritten_title,
                content=rewritten_content,
                categories=categories_to_publish,
                image_url=image_url,
            )

        except Exception as e:
            print(
                f"❌ حدث خطأ أثناء النشر في WordPress: {e}"
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_publish += 1
            skipped_count += 1
            continue

        time.sleep(2)

        # ==================================================
        # نجاح النشر
        # ==================================================

        if site_url:
            print(
                f"✅ تم النشر بنجاح: {site_url}"
            )

            mark_db_record(
                source_link,
                source_title,
                "published",
            )

            published_items.append(
                {
                    "title": rewritten_title,
                    "source_url": resolved_url,
                    "site_url": site_url,
                }
            )

        else:
            print(
                "❌ فشل النشر في WordPress."
            )

            print(
                "🔄 سيتم تسجيل الفشل وإعادة محاولة الخبر "
                "في الدورة القادمة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_publish += 1
            skipped_count += 1

    # ======================================================
    # تقرير الدورة
    # ======================================================

    print(
        "\n📊 تفاصيل نتائج الدورة:"
    )

    print(
        f"✅ نُشر بنجاح: "
        f"{len(published_items)}"
    )

    print(
        f"⚠️ فشل استخراج المقال: "
        f"{failed_extraction}"
    )

    print(
        f"🤖 فشل معالجة الذكاء الاصطناعي: "
        f"{failed_ai}"
    )

    print(
        f"❌ فشل النشر في WordPress: "
        f"{failed_publish}"
    )

    print(
        f"🚫 نطاقات ممنوعة: "
        f"{blocked_domain}"
    )

    print(
        f"🚫 استُبعد بسبب العنوان: "
        f"{len(filtered_title_items)}"
    )

    print(
        f"🔍 الأخبار المقبولة ضمن نافذة 3 ساعات: "
        f"{checked_count}"
    )

    print(
        f"❌ إجمالي الفشل/التجاوز: "
        f"{skipped_count}"
    )

    if quota_exhausted:
        print(
            "⏸️ توقفت الدورة بسبب عدم توفر "
            "حصة Gemini لأي من النماذج المتاحة."
        )

    send_cycle_report(
        published_items,
        checked_count,
        skipped_count,
        filtered_title_items,
    )

    print(
        "\n🎉 اكتملت الدورة بنجاح."
    )


if __name__ == "__main__":
    try:
        run_pipeline()

    except Exception as e:
        error_msg = (
            f"حدث خطأ غير متوقع أثناء تنفيذ الدورة: {e}"
        )

        print(
            f"💥 {error_msg}"
        )

        send_error_alert(
            error_msg
        )

        sys.exit(1)
