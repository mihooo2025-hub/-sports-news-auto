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

import db
from config import CONFIG
from rss_fetcher import fetch_prioritized_news
from article_extractor import extract_article
from content_ai import process_article
from wordpress_publisher import publish_post, test_authentication
from telegram_reporter import send_cycle_report, send_error_alert


def mark_db_record(url: str, title: str, status: str):
    """
    تسجيل حالة الخبر في قاعدة البيانات.

    publish_failed:
        فشل مؤقت، ويعاد محاولة الخبر في الدورات التالية
        لمدة أقصاها 6 ساعات حسب منطق db.py.

    published:
        الخبر تمت معالجته ونشره بنجاح.

    skipped_blocked_domain:
        خبر تم تجاوزه نهائيًا بسبب نطاق ممنوع.
    """

    db.mark_processed(
        url=url,
        title=title,
        status=status,
    )


def map_category_names_to_ids(category_names: list) -> list:
    configured = CONFIG.get("categories", [])

    if not isinstance(configured, list):
        return category_names

    return [
        category
        for category in category_names
        if category in configured
    ]


def run_pipeline():
    print("🚀 بدء دورة جلب ونشر الأخبار الرياضية...")

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

    news_items = fetch_prioritized_news()
    checked_count = len(news_items)

    if not news_items:
        print(
            "ℹ️ لم يتم العثور على أخبار جديدة في هذه الدورة."
        )

        send_cycle_report([], 0, 0)
        return

    print(
        f"🔍 تم العثور على {checked_count} "
        "خبر جديد للمعالجة."
    )

    published_items = []
    skipped_count = 0

    failed_extraction = 0
    failed_ai = 0
    failed_publish = 0
    blocked_domain = 0

    for idx, item in enumerate(news_items, start=1):

        source_title = item.get("title", "")
        source_link = item.get("link", "")
        matched_keyword = item.get("matched_keyword", "")

        print(
            f"\n[{idx}/{checked_count}] "
            f"جاري معالجة الخبر: {source_title}"
        )

        try:
            extracted_data = extract_article(source_link)

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

        if extracted_data.get("blocked"):
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

        raw_content = extracted_data.get("text", "")

        image_url = (
            extracted_data.get("image_url")
            or extracted_data.get("image")
            or item.get("image_url")
        )

        resolved_url = (
            extracted_data.get("resolved_url")
            or source_link
        )

        if not extracted_data.get("success") or not raw_content:
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

        time.sleep(5)

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

        if not rewritten_title or not rewritten_content:
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

        categories_to_publish = map_category_names_to_ids(
            category_names
        )

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

        if site_url:
            print(
                f"✅ تم النشر بنجاح مع الصورة: {site_url}"
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

    print("\n📊 تفاصيل نتائج الدورة:")
    print(f"✅ نُشر بنجاح: {len(published_items)}")
    print(f"⚠️ فشل استخراج المقال: {failed_extraction}")
    print(f"🤖 فشل معالجة الذكاء الاصطناعي: {failed_ai}")
    print(f"❌ فشل النشر في WordPress: {failed_publish}")
    print(f"🚫 نطاقات ممنوعة: {blocked_domain}")
    print(f"🔍 إجمالي الأخبار المفحوصة: {checked_count}")
    print(f"❌ إجمالي الفشل/التجاوز: {skipped_count}")

    send_cycle_report(
        published_items,
        checked_count,
        skipped_count,
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

        print(f"💥 {error_msg}")

        send_error_alert(error_msg)

        sys.exit(1)
