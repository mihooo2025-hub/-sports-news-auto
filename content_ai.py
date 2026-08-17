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
from difflib import SequenceMatcher
from google import genai
from google.genai import types
from config import CONFIG

client = genai.Client(api_key=CONFIG["gemini"]["api_key"])
MODEL = CONFIG["gemini"].get("model", "gemini-3.6-flash")

BLOCKED_CATEGORIES = {"اهم الاخبار", "مقالات وتحليلات"}
ALLOWED_CATEGORIES = [c for c in CONFIG["categories"] if c not in BLOCKED_CATEGORIES]

RULES_FILE = os.path.join(os.path.dirname(__file__), "rules_ar.md")

with open(RULES_FILE, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{{ALLOWED_CATEGORIES}}",
    json.dumps(ALLOWED_CATEGORIES, ensure_ascii=False)
)


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

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )

        result = json.loads(response.text)

    except Exception as e:
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
