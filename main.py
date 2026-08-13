"""
main.py
=======
الملف الرئيسي لإدارة دورة جلب الأخبار، معالجتها عبر الذكاء الاصطناعي،
نشرها في ووردبريس، وإرسال تقارير المتابعة عبر تلجرام.
"""

import sys
import db
from rss_fetcher import fetch_prioritized_news
from article_scraper import fetch_full_article
from content_ai import process_article
from category_mapper import map_category_names_to_ids
from wordpress_publisher import publish_post
from telegram_reporter import send_cycle_report, send_error_alert


def run_pipeline():
    print("🚀 بدء دورة جلب ونشر الأخبار الرياضية...")
    
    # تهيئة قاعدة البيانات Local DB
    db.init_db()

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

        # 2. كشط نص المقال الأصلي
        raw_content = fetch_full_article(source_link)
        if not raw_content:
            print("⚠️ تعذر جلب محتوى المقال — سيتم التجاوز.")
            db.mark_as_processed(source_link, source_title, status="skipped_no_content")
            skipped_count += 1
            continue

        # 3. إعادة الصياغة وإنشاء العنوان بالتصنيفات بواسطة OpenAI
        ai_result = process_article(raw_content, source_title, matched_keyword)
        if not ai_result:
            print("⚠️ فشلت معالجة المقال بواسطة الذكاء الاصطناعي — سيتم التجاوز.")
            db.mark_as_processed(source_link, source_title, status="skipped_ai_error")
            skipped_count += 1
            continue

        rewritten_title = ai_result["title"]
        rewritten_content = ai_result["rewritten_content"]
        category_names = ai_result.get("categories", [])

        # 4. مطابقة أسماء التصنيفات بـ IDs في ووردبريس
        category_ids = map_category_names_to_ids(category_names)

        # 5. نشر المقال في ووردبريس والحصول على رابط المقال الجديد
        site_url = publish_post(
            title=rewritten_title,
            content=rewritten_content,
            categories=category_ids
        )

        if site_url:
            print(f"✅ تم النشر بنجاح: {site_url}")
            db.mark_as_processed(source_link, rewritten_title, status="published")
            published_items.append({
                "title": rewritten_title,
                "source_url": source_link,
                "site_url": site_url,
            })
        else:
            print("❌ فشل النشر في ووردبريس.")
            db.mark_as_processed(source_link, source_title, status="publish_failed")
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
