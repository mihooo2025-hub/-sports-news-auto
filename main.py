"""
main.py
=======
الملف الرئيسي لإدارة دورة جلب الأخبار، معالجتها عبر الذكاء الاصطناعي،
نشرها في ووردبريس مع الصور البارزة، وإرسال تقارير المتابعة عبر تلجرام.
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
    """دالة مرنة لتسجيل الخبر في قاعدة البيانات بحسب اسم الدالة المتاح في db.py"""
    if hasattr(db, "mark_as_processed"):
        db.mark_as_processed(url, title, status)
    elif hasattr(db, "add_processed_news"):
        db.add_processed_news(url, title, status)
    elif hasattr(db, "save_article"):
        db.save_article(url, title, status)
    else:
        try:
            if hasattr(db, "is_processed"):
                db.is_processed(url)
        except Exception:
            pass


def map_category_names_to_ids(category_names: list) -> list:
    """
    إعادة التصنيفات المحددة بواسطة الذكاء الاصطناعي.
    إذا كانت التصنيفات في config قائمة أسماء، نمرر الأسماء المتقاطعة مباشرة لموديول النشر.
    """
    configured_categories = CONFIG.get("categories", [])
    if isinstance(configured_categories, list):
        # تصفية الأسماء المطابقة فقط الموجودة في config
        matched = [cat for cat in category_names if cat in configured_categories]
        return matched if matched else category_names
    return category_names


def run_pipeline():
    print("🚀 بدء دورة جلب ونشر الأخبار الرياضية...")

    if hasattr(db, "init_db"):
        db.init_db()

    # التحقق من WordPress قبل بدء معالجة الأخبار
    if not test_authentication():
        print("❌ تعذر الوصول إلى WordPress — تم إيقاف الدورة.")
        send_error_alert(
            "❌ تعذر الوصول إلى WordPress REST API. "
            "تحقق من بيانات الدخول أو HTTP 403 / Bot Verification."
        )
        return

    # 1. جلب قائمة الأخبار غير المكررة
    news_items = fetch_prioritized_news()
    checked_count = len(news_items)

    if not news_items:
        print("ℹ️ لم يتم العثور على أخبار جديدة في هذه الدورة.")
        send_cycle_report([], 0, 0)
        return

    print(f"🔍 تم العثور على {checked_count} خبر جديد للبدء في المعالجة...")

    published_items = []
    skipped_count = 0

    for idx, item in enumerate(news_items, start=1):
        source_title = item.get("title", "")
        source_link = item.get("link", "")
        matched_keyword = item.get("matched_keyword", "")

        print(f"\n[{idx}/{checked_count}] جاري معالجة الخبر: {source_title}")

        # 2. استخراج المقال ورابطه الأصلي والصورة
        extracted_data = extract_article(source_link)

        if extracted_data.get("blocked"):
            print("🚫 تجاوز الخبر لأنه ينتمي لنطاق ممنوع.")
            mark_db_record(source_link, source_title, "skipped_blocked_domain")
            skipped_count += 1
            continue

        raw_content = extracted_data.get("text", "")
        image_url = extracted_data.get("image_url") or extracted_data.get("image") or item.get("image_url")
        resolved_url = extracted_data.get("resolved_url") or source_link

        if not extracted_data.get("success") or not raw_content:
            print("⚠️ تعذر جلب محتوى المقال أو المحتوى قصير جدًا — سيتم التجاوز.")
            mark_db_record(source_link, source_title, "skipped_no_content")
            skipped_count += 1
            continue

        # 3. إعادة الصياغة وإنشاء العنوان والتصنيفات بواسطة الذكاء الاصطناعي
        ai_result = process_article(raw_content, source_title, matched_keyword)

        # تأخير ثانيتين بعد كل عملية إعادة صياغة لمزيد من الدقة وتجنب الأخطاء
        time.sleep(2)

        if not ai_result:
            print("⚠️ فشلت معالجة المقال بواسطة الذكاء الاصطناعي — سيتم التجاوز.")
            mark_db_record(source_link, source_title, "skipped_ai_error")
            skipped_count += 1
            continue

        rewritten_title = ai_result["title"]
        rewritten_content = ai_result["rewritten_content"]
        category_names = ai_result.get("categories", [])

        # 4. مطابقة التصنيفات
        categories_to_publish = map_category_names_to_ids(category_names)

        # 5. نشر المقال في ووردبريس مع رفع الصورة البارزة
        site_url = publish_post(
            title=rewritten_title,
            content=rewritten_content,
            categories=categories_to_publish,
            image_url=image_url
        )

        # تأخير ثانيتين بعد كل عملية نشر في ووردبريس لمزيد من الدقة وتجنب الأخطاء
        time.sleep(2)

        if site_url:
            print(f"✅ تم النشر بنجاح مع الصورة: {site_url}")
            mark_db_record(source_link, rewritten_title, "published")
            published_items.append({
                "title": rewritten_title,
                "source_url": resolved_url,
                "site_url": site_url,
            })
        else:
            print("❌ فشل النشر في ووردبريس.")
            mark_db_record(source_link, source_title, "publish_failed")
            skipped_count += 1

    # 6. إرسال تقرير الدورة إلى تلجرام
    send_cycle_report(published_items, checked_count, skipped_count)
    print("\n🎉 اكتملت الدورة بنجاح.")


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        error_msg = f"حدث خطأ غير متوقع أثناء تنفيذ الدورة: {e}"
        print(f"💥 {error_msg}")
        send_error_alert(error_msg)
        sys.exit(1)
