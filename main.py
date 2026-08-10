"""
main.py
=======
الملف الرئيسي — شغّله من Pydroid 3 أو عبر GitHub Actions.

يقوم بدورة كاملة:
1. فحص المصادقة مع ووردبريس أولًا (لتفادي إهدار استدعاءات OpenAI المدفوعة).
2. جلب الأخبار من Google News RSS (عربية + عالمية)، بحد أقصى للفحص (100 افتراضيًا).
3. تجاوز الأخبار المُعالجة سابقًا (عبر news.db) والمصادر الممنوعة.
4. استخراج النص الكامل والصورة البارزة من كل خبر.
5. الترجمة (إن لزم) وإعادة الصياغة والتصنيف والعنوان عبر OpenAI.
6. النشر كمسودة في ووردبريس، بحد أقصى للنشر (20 افتراضيًا).
7. إرسال تقرير بعناوين وروابط الأخبار المنشورة إلى مجموعة تلجرام.
"""

import time

from config import CONFIG
from db import is_processed, mark_processed
from rss_fetcher import fetch_prioritized_news
from article_extractor import extract_article
from content_ai import process_article
from wordpress_publisher import create_draft_post, test_authentication
from telegram_reporter import send_cycle_report, send_error_alert


def run_cycle():
    print("=" * 60)
    print("🔄 بدء دورة جلب ونشر جديدة...")
    print("=" * 60)

    # فحص المصادقة مع ووردبريس أولًا — لتفادي إهدار استدعاءات OpenAI المدفوعة
    if not test_authentication():
        error_msg = "فشلت المصادقة مع ووردبريس — تم إيقاف الدورة قبل استهلاك أي رصيد OpenAI."
        print(f"\n⛔ {error_msg}")
        send_error_alert(error_msg)
        return

    max_checked = CONFIG["fetch_settings"].get("max_articles_checked_per_cycle", 100)
    max_published = CONFIG["fetch_settings"].get("max_articles_published_per_cycle", 20)

    news_items = fetch_prioritized_news()
    print(f"📰 تم جلب {len(news_items)} خبر محتمل للفحص (الحد الأقصى: {max_checked}).")

    published_items = []
    checked_count = 0
    skipped_count = 0

    for item in news_items:
        if checked_count >= max_checked:
            print(f"⏸️ تم الوصول لحد الفحص الأقصى ({max_checked} خبر).")
            break
        if len(published_items) >= max_published:
            print(f"⏸️ تم الوصول لحد النشر الأقصى ({max_published} مقال).")
            break

        link = item["link"]
        title = item["title"]

        if is_processed(link):
            continue

        checked_count += 1
        print(f"\n➡️ [{checked_count}/{max_checked}] معالجة: {title}")

        article_data = extract_article(link)

        if article_data.get("blocked"):
            mark_processed(link, title=title, status="skipped_blocked_domain")
            skipped_count += 1
            continue

        if not article_data["success"]:
            print("⏭️ تم تجاوز الخبر — تعذر استخراج نص كافٍ منه.")
            mark_processed(link, title=title, status="skipped_no_content")
            skipped_count += 1
            continue

        ai_result = process_article(
            raw_text=article_data["text"],
            source_title=title,
            matched_keyword=item.get("matched_keyword", ""),
        )
        if not ai_result:
            print("⏭️ تم تجاوز الخبر — فشلت معالجة الذكاء الاصطناعي.")
            mark_processed(link, title=title, status="skipped_ai_failed")
            skipped_count += 1
            continue

        post_data = create_draft_post(
            ai_result=ai_result,
            source_url=article_data["resolved_url"],
            image_url=article_data["image_url"],
        )

        if post_data:
            mark_processed(link, title=title, wp_post_id=post_data.get("id"), status="published_draft")
            published_items.append({
                "title": ai_result["title"],
                "source_url": article_data["resolved_url"],
                "wp_link": post_data.get("link", ""),
            })
        else:
            mark_processed(link, title=title, status="skipped_wp_failed")
            skipped_count += 1

        time.sleep(2)  # مهلة بسيطة بين الطلبات

    print(f"\n✅ انتهت الدورة — نُشر {len(published_items)} مسودة، فُحص {checked_count}، تُجووِز {skipped_count}.")

    send_cycle_report(published_items, checked_count, skipped_count)


if __name__ == "__main__":
    run_cycle()
    # لتشغيل دوري تلقائي كل ساعة عند التشغيل محليًا على Pydroid (غير مطلوب عند
    # الاعتماد على GitHub Actions، فهو يستدعي run_cycle() مرة واحدة كل تشغيل):
    #
    # while True:
    #     run_cycle()
    #     print("⏳ انتظار 60 دقيقة قبل الدورة التالية...")
    #     time.sleep(3600)
