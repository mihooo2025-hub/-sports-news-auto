"""
main.py
=======
إدارة دورة جلب الأخبار من كووورة ومعالجتها ونشرها كمسودات في WordPress.

المشروع يعتمد على:
- rss_fetcher.py لجلب الأخبار
- article_extractor.py لاستخراج نص الخبر والصورة البارزة
- content_ai.py لإعادة الصياغة عبر Gemini
- wordpress_publisher.py لإنشاء المسودة
- db.py لمنع التكرار وإعادة محاولة الأخبار الفاشلة
- telegram_reporter.py لإرسال تقرير الدورة

الأخبار التي تفشل أثناء:
- استخراج المقال
- معالجة الذكاء الاصطناعي
- النشر في WordPress

تسجل كـ publish_failed لتتم إعادة محاولتها في الدورة التالية
وفق سياسة إعادة المحاولة الموجودة في db.py.
"""

import sys
import time
from datetime import datetime, timezone

import db

from rss_fetcher import fetch_prioritized_news
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


# =========================================================
# Database helpers
# =========================================================

def mark_db_record(
    url: str,
    title: str,
    status: str,
) -> None:
    """
    تسجيل حالة الخبر في قاعدة البيانات.
    """

    db.mark_processed(
        url=url,
        title=title,
        status=status,
    )


# =========================================================
# Categories
# =========================================================

def clean_categories(category_names) -> list[str]:
    """
    تنظيف التصنيفات القادمة من Gemini.

    يمنع:
    - أهم الاخبار
    - أهم الأخبار
    - مقالات وتحليلات
    - مقالات وتقارير

    ويترك فقط التصنيفات الصحيحة التي ستقوم
    wordpress_publisher.py بمطابقتها مع تصنيفات الموقع.
    """

    if not isinstance(category_names, list):
        return []

    forbidden = {
        "أهم الاخبار",
        "أهم الأخبار",
        "مقالات وتحليلات",
        "مقالات وتقارير",
    }

    result = []
    seen = set()

    for category in category_names:
        if not isinstance(category, str):
            continue

        category = category.strip()

        if not category:
            continue

        if category in forbidden:
            continue

        if category in seen:
            continue

        seen.add(category)
        result.append(category)

        if len(result) >= 3:
            break

    return result


# =========================================================
# Main pipeline
# =========================================================

def run_pipeline() -> None:

    # -----------------------------------------------------
    # تثبيت وقت بداية الدورة
    # -----------------------------------------------------

    cycle_start = datetime.now(timezone.utc)

    print("=" * 70)
    print("🚀 بدء دورة جلب ونشر الأخبار الرياضية")
    print(
        f"🕐 وقت بداية الدورة: {cycle_start.isoformat()}"
    )
    print("=" * 70)

    # -----------------------------------------------------
    # تهيئة قاعدة البيانات
    # -----------------------------------------------------

    db.init_db()

    # -----------------------------------------------------
    # اختبار WordPress قبل بدء المعالجة
    # -----------------------------------------------------

    if not test_authentication():

        error_message = (
            "❌ تعذر الوصول إلى WordPress REST API. "
            "تحقق من بيانات الموقع واسم المستخدم "
            "وكلمة مرور التطبيق."
        )

        print(error_message)

        send_error_alert(error_message)

        return

    # -----------------------------------------------------
    # جلب الأخبار
    #
    # نافذة الوقت يحددها rss_fetcher.py
    # -----------------------------------------------------

    print("\n🔍 جاري البحث عن الأخبار الجديدة...")

    try:

        news_items = fetch_prioritized_news(
            cycle_start=cycle_start
        )

    except Exception as exc:

        error_message = (
            f"❌ حدث خطأ أثناء جلب الأخبار: {exc}"
        )

        print(error_message)

        send_error_alert(error_message)

        return

    checked_count = len(news_items)

    print(
        f"📰 تم العثور على {checked_count} خبر جديد."
    )

    # -----------------------------------------------------
    # لا توجد أخبار
    # -----------------------------------------------------

    if not news_items:

        print(
            "ℹ️ لا توجد أخبار جديدة قابلة للمعالجة في هذه الدورة."
        )

        send_cycle_report(
            [],
            0,
            0,
        )

        print("\n🎉 اكتملت الدورة بنجاح.")

        return

    # -----------------------------------------------------
    # Counters
    # -----------------------------------------------------

    published_items = []

    skipped_count = 0

    failed_extraction = 0
    failed_ai = 0
    failed_publish = 0

    blocked_domain = 0
    no_image = 0
    already_processed = 0

    quota_exhausted = False

    # -----------------------------------------------------
    # معالجة الأخبار
    # -----------------------------------------------------

    for index, item in enumerate(
        news_items,
        start=1,
    ):

        source_title = (
            item.get("title") or ""
        ).strip()

        source_link = (
            item.get("link")
            or item.get("url")
            or ""
        ).strip()

        matched_keyword = (
            item.get("matched_keyword")
            or ""
        ).strip()

        if not source_link:

            print(
                f"\n[{index}/{checked_count}] "
                "⚠️ تم تجاوز عنصر بدون رابط."
            )

            skipped_count += 1

            continue

        print("\n" + "-" * 70)

        print(
            f"[{index}/{checked_count}] "
            f"جاري معالجة الخبر:"
        )

        print(
            f"العنوان: {source_title}"
        )

        print(
            f"الرابط: {source_link}"
        )

        # -------------------------------------------------
        # منع التكرار
        # -------------------------------------------------

        try:

            if db.is_processed(
                url=source_link,
                title=source_title,
            ):

                print(
                    "⏭️ تم تجاوز الخبر لأنه منشور أو تمت معالجته سابقًا."
                )

                already_processed += 1

                continue

        except AttributeError:

            # إذا كانت دالة is_processed غير موجودة
            # لا نوقف المشروع بسبب ذلك.
            pass

        except Exception as exc:

            print(
                f"⚠️ تعذر فحص حالة التكرار: {exc}"
            )

        # -------------------------------------------------
        # استخراج المقال
        # -------------------------------------------------

        print(
            "📄 جاري استخراج نص الخبر والصورة..."
        )

        try:

            extracted_data = extract_article(
                source_link
            )

        except Exception as exc:

            print(
                f"⚠️ حدث خطأ أثناء استخراج المقال: {exc}"
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_extraction += 1
            skipped_count += 1

            continue

        if not isinstance(
            extracted_data,
            dict,
        ):

            print(
                "⚠️ نتيجة استخراج المقال غير صحيحة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_extraction += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # نطاق ممنوع
        # -------------------------------------------------

        if extracted_data.get("blocked"):

            print(
                "🚫 تم تجاوز الخبر لأنه ينتمي إلى نطاق ممنوع."
            )

            mark_db_record(
                source_link,
                source_title,
                "skipped_blocked_domain",
            )

            blocked_domain += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # استخراج المحتوى
        # -------------------------------------------------

        raw_content = (
            extracted_data.get("text")
            or extracted_data.get("content")
            or ""
        ).strip()

        resolved_url = (
            extracted_data.get("resolved_url")
            or extracted_data.get("url")
            or source_link
        )

        # -------------------------------------------------
        # استخراج الصورة
        # -------------------------------------------------

        image_url = (
            extracted_data.get("image_url")
            or extracted_data.get("featured_image")
            or extracted_data.get("image")
            or item.get("image_url")
            or ""
        )

        if isinstance(image_url, str):
            image_url = image_url.strip()

        # -------------------------------------------------
        # فشل استخراج النص
        # -------------------------------------------------

        if (
            not extracted_data.get("success")
            or not raw_content
        ):

            print(
                "⚠️ تعذر جلب محتوى المقال."
            )

            print(
                "🔄 سيتم إعادة محاولة الخبر في دورة لاحقة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_extraction += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # عدم وجود صورة بارزة
        #
        # حسب طلب المشروع:
        # يتم تجاهل الخبر إذا لم توجد صورة.
        # -------------------------------------------------

        if not image_url:

            print(
                "🖼️ لا توجد صورة بارزة للخبر."
            )

            print(
                "⏭️ سيتم تجاهل الخبر حسب إعدادات المشروع."
            )

            mark_db_record(
                source_link,
                source_title,
                "skipped_no_image",
            )

            no_image += 1
            skipped_count += 1

            continue

        print(
            f"📄 تم استخراج {len(raw_content.split())} كلمة تقريبًا."
        )

        # -------------------------------------------------
        # إعادة الصياغة عبر Gemini
        # -------------------------------------------------

        print(
            "🤖 جاري إرسال الخبر إلى Gemini..."
        )

        try:

            ai_result = process_article(
                raw_content,
                source_title,
                matched_keyword,
            )

        except Exception as exc:

            print(
                f"⚠️ حدث خطأ أثناء معالجة Gemini: {exc}"
            )

            # فحص الحصة قبل اعتبار الخبر فاشلاً
            if is_gemini_quota_exhausted():

                print(
                    "⛔ تم استنفاد جميع مفاتيح Gemini المتاحة."
                )

                print(
                    "⏹️ سيتم إيقاف الدورة دون تسجيل الأخبار المتبقية كفاشلة."
                )

                quota_exhausted = True

                break

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # التأكد من عدم نفاد جميع الحصص
        # -------------------------------------------------

        if is_gemini_quota_exhausted():

            print(
                "⛔ لم يعد أي مفتاح Gemini متاحًا في هذه الدورة."
            )

            quota_exhausted = True

            break

        # -------------------------------------------------
        # نتيجة Gemini فارغة
        # -------------------------------------------------

        if not ai_result:

            print(
                "⚠️ فشلت إعادة صياغة الخبر."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1

            continue

        if not isinstance(
            ai_result,
            dict,
        ):

            print(
                "⚠️ Gemini أعاد نتيجة غير صحيحة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1

            continue

        rewritten_title = (
            ai_result.get("title")
            or ""
        ).strip()

        rewritten_content = (
            ai_result.get("rewritten_content")
            or ai_result.get("content")
            or ""
        ).strip()

        category_names = (
            ai_result.get("categories")
            or []
        )

        # -------------------------------------------------
        # التحقق من النتيجة
        # -------------------------------------------------

        if (
            not rewritten_title
            or not rewritten_content
        ):

            print(
                "⚠️ نتيجة الذكاء الاصطناعي ناقصة."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_ai += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # تنظيف التصنيفات
        # -------------------------------------------------

        categories_to_publish = clean_categories(
            category_names
        )

        print(
            f"🏷️ التصنيفات المختارة: "
            f"{categories_to_publish or 'بدون تصنيف إضافي'}"
        )

        # -------------------------------------------------
        # فارق 10 ثوانٍ بين عمليات إعادة الصياغة
        #
        # ننتظر قبل الانتقال إلى الخبر التالي،
        # وليس قبل نشر الخبر الحالي.
        # -------------------------------------------------

        # -------------------------------------------------
        # النشر في WordPress
        # -------------------------------------------------

        print(
            "📝 جاري إنشاء مسودة في WordPress..."
        )

        try:

            site_url = publish_post(
                title=rewritten_title,
                content=rewritten_content,
                categories=categories_to_publish,
                image_url=image_url,
            )

        except Exception as exc:

            print(
                f"❌ حدث خطأ أثناء النشر في WordPress: {exc}"
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_publish += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # نجاح النشر
        # -------------------------------------------------

        if site_url:

            print(
                f"✅ تم إنشاء المسودة بنجاح: {site_url}"
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

            # ---------------------------------------------
            # فارق 3 ثوانٍ بين كل عملية نشر
            # ---------------------------------------------

            if index < checked_count:

                print(
                    "⏳ انتظار 3 ثوانٍ بعد النشر..."
                )

                time.sleep(3)

        else:

            print(
                "❌ لم يتم إنشاء المسودة في WordPress."
            )

            mark_db_record(
                source_link,
                source_title,
                "publish_failed",
            )

            failed_publish += 1
            skipped_count += 1

            continue

        # -------------------------------------------------
        # فارق 10 ثوانٍ قبل إعادة الصياغة التالية
        # -------------------------------------------------

        if index < checked_count:

            print(
                "⏳ انتظار 10 ثوانٍ قبل معالجة الخبر التالي..."
            )

            time.sleep(10)

    # =====================================================
    # نهاية الدورة
    # =====================================================

    print("\n" + "=" * 70)
    print("📊 تقرير دورة الأخبار")
    print("=" * 70)

    print(
        f"🔍 تم فحص: {checked_count}"
    )

    print(
        f"✅ تم إنشاء مسودات: {len(published_items)}"
    )

    print(
        f"📄 فشل استخراج المقال: {failed_extraction}"
    )

    print(
        f"🤖 فشل الذكاء الاصطناعي: {failed_ai}"
    )

    print(
        f"📝 فشل النشر في WordPress: {failed_publish}"
    )

    print(
        f"🖼️ بدون صورة بارزة: {no_image}"
    )

    print(
        f"🚫 نطاقات ممنوعة: {blocked_domain}"
    )

    print(
        f"🔁 مكررة/معالجة سابقًا: {already_processed}"
    )

    print(
        f"❌ إجمالي الفشل/التجاوز: {skipped_count}"
    )

    if quota_exhausted:

        print(
            "⏸️ توقفت الدورة بسبب نفاد حصة جميع مفاتيح Gemini."
        )

    # =====================================================
    # إرسال تقرير Telegram
    # =====================================================

    try:

        send_cycle_report(
            published_items,
            checked_count,
            skipped_count,
        )

        print(
            "📨 تم إرسال تقرير الدورة إلى Telegram."
        )

    except Exception as exc:

        print(
            f"⚠️ تعذر إرسال تقرير Telegram: {exc}"
        )

    print("\n🎉 اكتملت الدورة بنجاح.")


# =========================================================
# Entry point
# =========================================================

if __name__ == "__main__":

    try:

        run_pipeline()

    except KeyboardInterrupt:

        print(
            "\n⏹️ تم إيقاف المشروع يدويًا."
        )

        sys.exit(0)

    except Exception as exc:

        error_message = (
            f"💥 حدث خطأ غير متوقع أثناء تنفيذ الدورة: {exc}"
        )

        print(error_message)

        try:

            send_error_alert(
                error_message
            )

        except Exception:

            pass

        sys.exit(1)
