"""
telegram_reporter.py
=====================
Sends a summary report to a Telegram group via Bot after each execution cycle:
- Titles of rewritten and published articles.
- Links to original source articles and published site articles.
- Number of articles that failed or were skipped during processing.
- Number of articles excluded because of their titles.

Requires Telegram Bot Token (from BotFather) and Group ID (chat_id) in config.json
or provided via environment variables (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID).
"""

import html
import requests
from config import CONFIG


TG = CONFIG.get(
    "telegram",
    {},
)

BOT_TOKEN = TG.get(
    "bot_token",
    "" 
)

CHAT_ID = TG.get(
    "chat_id",
    "",
)

API_URL = (
    f"https://api.telegram.org/bot"
    f"{BOT_TOKEN}/sendMessage"
)

# الحد الأقصى الذي تسمح به Telegram للرسالة النصية تقريبًا.
MAX_MESSAGE_LENGTH = 4096


def _is_configured() -> bool:
    return (
        bool(BOT_TOKEN)
        and bool(CHAT_ID)
        and "PASTE_YOUR" not in BOT_TOKEN
        and "PASTE_YOUR" not in CHAT_ID
    )


def _send_message(
    text: str,
) -> bool:

    try:
        resp = requests.post(
            API_URL,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=15,
        )

        resp.raise_for_status()

        return True

    except Exception as e:
        print(
            f"⚠️ Failed to send Telegram report: {e}"
        )

        return False


def send_cycle_report(
    published_items: list,
    checked_count: int,
    skipped_count: int,
    filtered_title_items: list = None,
):
    """
    إرسال تقرير دورة الأخبار إلى Telegram.

    published_items:
        الأخبار التي تم نشرها بنجاح.

    checked_count:
        عدد الأخبار التي تم فحصها.

    skipped_count:
        عدد الأخبار التي فشلت أو تم تجاوزها أثناء المعالجة.

    filtered_title_items:
        الأخبار التي تم استبعادها بسبب عنوانها.

        مهم:
        يتم استخدام هذا المتغير لحساب العدد فقط.
        لا يتم إرسال عناوين الأخبار المستبعدة إلى Telegram.

    ملاحظة:
        إذا تجاوز التقرير الحد الأقصى المسموح به لرسالة تلجرام واحدة
        (تقريبًا 4096 حرفًا)، يتم تقسيمه تلقائيًا إلى عدة رسائل متتالية
        بدل إلغاء الإرسال بالكامل.
    """

    if not _is_configured():

        print(
            "ℹ️ Telegram credentials not configured "
            "— skipping report dispatch."
        )

        return

    # =========================================================
    # حساب عدد الأخبار المستبعدة بسبب العنوان فقط
    # =========================================================

    filtered_title_count = len(
        filtered_title_items or []
    )

    # =========================================================
    # في حالة عدم نشر أي خبر
    # =========================================================

    if not published_items:

        message = (
            f"📊 <b>تقرير دورة الأخبار</b>\n\n"
            f"🔍 تم فحص: {checked_count} خبر\n"
            f"❌ فشل/تجاوز: {skipped_count} خبر\n"
            f"🚫 مستبعد بسبب العنوان: "
            f"{filtered_title_count} خبر\n"
            f"✅ تم النشر: 0 خبر\n\n"
            f"لم يُنشر أي خبر جديد في هذه الدورة."
        )

        # هذه الرسالة قصيرة دائمًا ولن تتجاوز الحد، لكن نتركها
        # محمية بنفس منطق الإرسال العادي.
        _send_message(
            message
        )

        return

    # =========================================================
    # رأس التقرير
    # =========================================================

    header = (
        f"📊 <b>تقرير دورة الأخبار</b>\n"
        f"✅ نُشر: {len(published_items)} | "
        f"🔍 فُحص: {checked_count} | "
        f"❌ فشل/تجاوز: {skipped_count}\n"
        f"🚫 مستبعد بسبب العنوان: "
        f"{filtered_title_count} خبر\n"
    )

    # =========================================================
    # بناء نص كل خبر منشور على حدة
    # =========================================================

    item_texts = []

    for i, item in enumerate(
        published_items,
        start=1,
    ):

        title = item.get(
            "title",
            "بدون عنوان",
        )

        source_url = item.get(
            "source_url",
            "غير متوفر",
        )

        site_url = (
            item.get("site_url")
            or item.get("post_url")
            or "غير متوفر"
        )

        title = html.escape(
            str(title)
        )

        # =====================================================
        # رابط الخبر الأصلي
        # =====================================================

        if (
            source_url
            and source_url != "غير متوفر"
        ):

            source_link = (
                f'<a href="'
                f'{html.escape(str(source_url), quote=True)}'
                f'">رابط الخبر الأصلي</a>'
            )

        else:

            source_link = (
                "غير متوفر"
            )

        # =====================================================
        # رابط الخبر المنشور
        # =====================================================

        if (
            site_url
            and site_url != "غير متوفر"
        ):

            site_link = (
                f'<a href="'
                f'{html.escape(str(site_url), quote=True)}'
                f'">رابط الخبر الجديد</a>'
            )

        else:

            site_link = (
                "غير متوفر"
            )

        item_texts.append(
            f"\n{i}. <b>{title}</b>\n"
            f"🔗 المصدر الأصلي: {source_link}\n"
            f"🌐 الخبر الجديد: {site_link}"
        )

    # =========================================================
    # تجميع النصوص في رسائل بحيث لا تتجاوز أي رسالة الحد الأقصى.
    # الرأس (header) يوضع فقط في أول رسالة.
    # إذا كان هناك أكثر من رسالة، تُضاف علامة ترقيم (جزء س/ص)
    # في بداية كل رسالة تالية للرأس.
    # =========================================================

    messages = []

    current_chunk = header
    is_first_chunk = True

    for item_text in item_texts:

        # حالة نادرة: خبر واحد بمفرده أطول من الحد المسموح.
        # في هذه الحالة نرسله وحده كما هو (لن نقسّم داخل نفس الخبر)
        # مع تحذير في السجلات.
        if len(item_text) > MAX_MESSAGE_LENGTH:

            print(
                "⚠️ عنصر خبر واحد يتجاوز بمفرده حد رسالة "
                "Telegram — سيتم إرساله كما هو."
            )

            if current_chunk.strip():
                messages.append(current_chunk)

            messages.append(item_text)

            current_chunk = ""
            continue

        candidate = current_chunk + item_text

        if len(candidate) > MAX_MESSAGE_LENGTH:

            # أغلق الرسالة الحالية وابدأ رسالة جديدة بهذا العنصر.
            messages.append(current_chunk)

            current_chunk = item_text
            is_first_chunk = False

        else:

            current_chunk = candidate

    if current_chunk.strip():
        messages.append(current_chunk)

    total_parts = len(messages)

    # =========================================================
    # إرسال كل جزء على حدة، مع ترقيم الأجزاء إذا كانت أكثر من رسالة.
    # =========================================================

    for idx, part in enumerate(messages, start=1):

        if total_parts > 1:

            part_to_send = (
                f"{part}\n\n"
                f"📄 (الجزء {idx}/{total_parts})"
            )

        else:

            part_to_send = part

        sent = _send_message(
            part_to_send
        )

        if not sent:

            print(
                f"⚠️ فشل إرسال الجزء {idx}/{total_parts} "
                "من تقرير Telegram."
            )


def send_error_alert(
    message: str,
):
    """
    إرسال تنبيه فوري عند حدوث خطأ حرج.
    """

    if not _is_configured():
        return

    _send_message(
        f"⛔ <b>تنبيه خطأ — نظام الأخبار</b>\n\n"
        f"{html.escape(str(message))}"
    )
