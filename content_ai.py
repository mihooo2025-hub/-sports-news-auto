"""
content_ai.py
=============
يرسل نص الخبر الأصلي إلى Google Gemini ليقوم بـ:
1. الفحص النصي الأولي بواسطة بايثون + المراجعة الذكية عبر Gemini للتأكد من المعنى.
2. إعادة صياغته بأسلوب صحفي رياضي محترف.
3. اقتراح عنوان جذاب واختيار التصنيف المناسب.
"""

import json
import re
import os
import time
from difflib import SequenceMatcher
from google import genai
from google.genai import types
from config import CONFIG

MODEL = CONFIG["gemini"].get("model", "gemini-3.6-flash")

# جلب قائمة مفاتيح API (سواء مفتاح واحد أو عدة مفاتيح مفصولة بفاصلة)
raw_api_key = CONFIG["gemini"]["api_key"]
if isinstance(raw_api_key, list):
    API_KEYS = raw_api_key
elif isinstance(raw_api_key, str) and "," in raw_api_key:
    API_KEYS = [k.strip() for k in raw_api_key.split(",") if k.strip()]
else:
    API_KEYS = [raw_api_key]

current_key_index = 0

BLOCKED_CATEGORIES = {"اهم الاخبار", "مقالات وتحليلات"}
ALLOWED_CATEGORIES = [c for c in CONFIG["categories"] if c not in BLOCKED_CATEGORIES]

RULES_FILE = os.path.join(os.path.dirname(__file__), "rules_ar.md")

with open(RULES_FILE, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{{ALLOWED_CATEGORIES}}",
    json.dumps(ALLOWED_CATEGORIES, ensure_ascii=False)
)


def get_client():
    global current_key_index
    key = API_KEYS[current_key_index]
    return genai.Client(api_key=key)


def switch_to_next_key():
    global current_key_index
    if len(API_KEYS) > 1:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
        print(f"🔄 تم التبديل إلى مفتاح Gemini API رقم ({current_key_index + 1}/{len(API_KEYS)})")
        return True
    return False


def is_semantic_duplicate(new_title: str, recent_titles: list[str]) -> bool:
    """
    تم الغاء فحص التكرار.
    """
    return False


def process_article(raw_text: str, source_title: str, matched_keyword: str) -> dict | None:
    if not raw_text or len(raw_text) < 100:
        print("⚠️ تم تجاوز المقال — النص الأصلي قصير جدًا أو فارغ (لا يمكن الاعتماد عليه).")
        return None

    user_prompt = (
        f"عنوان الخبر كما ورد من المصدر: {source_title}\n"
        f"الكلمة المفتاحية المرتبطة بالبحث: {matched_keyword}\n\n"
        f"نص الخبر الأصلي الكامل:\n{raw_text}"
    )

    max_retries = max(3, len(API_KEYS) * 2)
    retry_delays = [5, 15, 30, 45, 60]

    for attempt in range(max_retries):
        try:
            client = get_client()
            response = client.models.generate_content(
                model=MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )

            result = json.loads(response.text)
            break

        except Exception as e:
            error_text = str(e)

            # أخطاء مؤقتة أو استنفاد الحصة
            temporary_errors = (
                "503",
                "UNAVAILABLE",
                "429",
                "RESOURCE_EXHAUSTED",
                "500",
                "INTERNAL",
                "502",
                "BAD_GATEWAY",
                "504",
                "DEADLINE_EXCEEDED",
            )

            is_quota_error = "429" in error_text or "RESOURCE_EXHAUSTED" in error_text.upper()
            is_temporary_error = any(error in error_text.upper() for error in temporary_errors)

            if is_quota_error:
                # التبديل للمفتاح الاحتياطي عند استنفاد الحصة
                switched = switch_to_next_key()
                if switched:
                    time.sleep(2)
                    continue

            if is_temporary_error and attempt < max_retries - 1:
                wait_time = retry_delays[min(attempt, len(retry_delays) - 1)]

                print(
                    f"⚠️ تعذر الاتصال بـ Google Gemini مؤقتًا "
                    f"(المحاولة {attempt + 1}/{max_retries}). "
                    f"سيتم إعادة المحاولة بعد {wait_time} ثانية..."
                )

                time.sleep(wait_time)
                continue

            print(f"❌ فشل استدعاء Google Gemini أو تحليل الرد: {e}")
            return None

    result["categories"] = [
        c for c in result.get("categories", [])
        if c in ALLOWED_CATEGORIES
    ]

    if not result["categories"]:
        print("⚠️ لم يتم تحديد تصنيف مناسب للخبر — سيُنشر بلا تصنيف (غير مصنف).")

    if not result.get("title"):
        print("⚠️ لم يتم توليد عنوان — سيتم تجاوز المقال.")
        return None

    return result
