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

        if len(message) > MAX_MESSAGE_LENGTH:

            print(
                "⚠️ تقرير Telegram تجاوز الحد المسموح به."
            )

            return

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

    lines = []

    # =========================================================
    # إضافة الأخبار المنشورة فقط
    # =========================================================

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

        lines.append(
            f"\n{i}. <b>{title}</b>\n"
            f"🔗 المصدر الأصلي: {source_link}\n"
            f"🌐 الخبر الجديد: {site_link}"
        )

    # =========================================================
    # رسالة واحدة فقط
    # =========================================================

    message = (
        header
        + "".join(lines)
    )

    # Telegram يسمح بحد أقصى يقارب 4096 حرفًا.
    if len(message) > MAX_MESSAGE_LENGTH:

        print(
            "⚠️ تقرير Telegram تجاوز الحد الأقصى "
            "لرسالة واحدة (4096 حرف تقريبًا)."
        )

        print(
            f"📏 طول التقرير: {len(message)} حرف."
        )

        print(
            "ℹ️ لن يتم تقسيم التقرير إلى عدة رسائل."
        )

        return

    _send_message(
        message
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
