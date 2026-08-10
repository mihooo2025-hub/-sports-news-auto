"""
telegram_reporter.py
=====================
يرسل تقريرًا إلى مجموعة تلجرام عبر بوت بعد كل دورة تشغيل، يحتوي:
- عنوان كل خبر أُعيدت صياغته ونُشر.
- رابط الخبر الأصلي الذي نُقل منه.

يحتاج توكن بوت تلجرام (من BotFather) ومعرّف المجموعة (chat_id) في config.json
أو عبر متغيرات البيئة TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID.
"""

import requests
from config import CONFIG

TG = CONFIG.get("telegram", {})
BOT_TOKEN = TG.get("bot_token", "")
CHAT_ID = TG.get("chat_id", "")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
MAX_MESSAGE_LENGTH = 3800  # هامش أمان تحت حد تلجرام (4096 حرفًا)


def _is_configured() -> bool:
    return bool(BOT_TOKEN) and bool(CHAT_ID) and "PASTE_YOUR" not in BOT_TOKEN and "PASTE_YOUR" not in CHAT_ID


def _send_message(text: str) -> bool:
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
        print(f"⚠️ فشل إرسال تقرير تلجرام: {e}")
        return False


def _chunk_message(lines: list, header: str) -> list:
    """يقسّم قائمة الأسطر إلى رسائل متعددة إذا تجاوزت حد تلجرام."""
    chunks = []
    current = header
    for line in lines:
        if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
            chunks.append(current)
            current = line
        else:
            current += "\n" + line
    if current:
        chunks.append(current)
    return chunks


def send_cycle_report(published_items: list, checked_count: int, skipped_count: int):
    """
    published_items: قائمة dict فيها {"title": عنوان المقال المُعاد صياغته,
                                        "source_url": رابط الخبر الأصلي,
                                        "wp_link": رابط المسودة في ووردبريس (اختياري)}
    """
    if not _is_configured():
        print("ℹ️ لم يتم إعداد تلجرام (bot_token/chat_id) — تم تخطي إرسال التقرير.")
        return

    if not published_items:
        _send_message(
            f"📊 <b>تقرير دورة نبض الملاعب</b>\n\n"
            f"تم فحص {checked_count} خبر، ولم يُنشر أي خبر جديد في هذه الدورة."
        )
        return

    header = (
        f"📊 <b>تقرير دورة نبض الملاعب</b>\n"
        f"✅ نُشر: {len(published_items)} | 🔍 فُحص: {checked_count} | ⏭️ تُجووِز: {skipped_count}\n"
    )

    lines = []
    for i, item in enumerate(published_items, start=1):
        title = item.get("title", "بدون عنوان")
        source_url = item.get("source_url", "")
        lines.append(f"\n{i}. <b>{title}</b>\n🔗 المصدر: {source_url}")

    for chunk in _chunk_message(lines, header):
        _send_message(chunk)


def send_error_alert(message: str):
    """يُستخدم لإرسال تنبيه فوري عند فشل حرج (مثل فشل المصادقة مع ووردبريس)."""
    if not _is_configured():
        return
    _send_message(f"⛔ <b>تنبيه خطأ — نبض الملاعب</b>\n\n{message}")
