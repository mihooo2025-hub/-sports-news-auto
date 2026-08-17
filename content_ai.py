"""
content_ai.py
=============
يرسل نص الخبر الأصلي إلى OpenAI ليقوم بـ:
1. الفحص النصي الأولي بواسطة بايثون + المراجعة الذكية عبر OpenAI للتأكد من المعنى.
2. إعادة صياغته بأسلوب صحفي رياضي محترف.
3. اقتراح عنوان جذاب واختيار التصنيف المناسب.
"""

import json
import re
import os
from difflib import SequenceMatcher
from openai import OpenAI
from config import CONFIG

client = OpenAI(api_key=CONFIG["openai"]["api_key"])
MODEL = CONFIG["openai"].get("model", "gpt-4o-mini")

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
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"❌ فشل استدعاء OpenAI أو تحليل الرد: {e}")
        return None

    result["categories"] = [c for c in result.get("categories", []) if c in ALLOWED_CATEGORIES]
    if not result["categories"]:
        print("⚠️ لم يتم تحديد تصنيف مناسب للخبر — سيُنشر بلا تصنيف (غير مصنف).")

    if not result.get("title"):
        print("⚠️ لم يتم توليد عنوان — سيتم تجاوز المقال.")
        return None

    return result
