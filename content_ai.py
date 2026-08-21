"""
content_ai.py
=============
يرسل نص الخبر الأصلي إلى Google Gemini ليقوم بـ:
1. الفحص النصي الأولي بواسطة بايثون + المراجعة الذكية عبر Gemini للتأكد من المعنى.
2. إعادة صياغته بأسلوب صحفي رياضي محترف.
3. اقتراح عنوان جذاب واختيار التصنيف المناسب.

نظام إعادة المحاولة:
- تتم محاولة كل مفتاح Gemini مرة واحدة فقط لكل خبر.
- عند فشل المفتاح الحالي يتم الانتقال مباشرة إلى المفتاح التالي.
- لا توجد فترات انتظار طويلة بين المحاولات.
- إذا فشلت جميع المفاتيح، يتم تجاوز الخبر ليعاد في الدورة التالية.
"""

import json
import os

from google import genai
from google.genai import types
from config import CONFIG


MODEL = CONFIG["gemini"].get(
    "model",
    "gemini-3.6-flash",
)


# ==========================================================
# جلب قائمة مفاتيح Gemini API
# ==========================================================

raw_api_key = CONFIG["gemini"]["api_key"]

if isinstance(raw_api_key, list):
    API_KEYS = [
        k.strip()
        for k in raw_api_key
        if isinstance(k, str) and k.strip()
    ]

elif isinstance(raw_api_key, str) and "," in raw_api_key:
    API_KEYS = [
        k.strip()
        for k in raw_api_key.split(",")
        if k.strip()
    ]

else:
    API_KEYS = [raw_api_key] if raw_api_key else []


current_key_index = 0


# ==========================================================
# التصنيفات
# ==========================================================

BLOCKED_CATEGORIES = {
    "اهم الاخبار",
    "مقالات وتحليلات",
}

ALLOWED_CATEGORIES = [
    c
    for c in CONFIG["categories"]
    if c not in BLOCKED_CATEGORIES
]


# ==========================================================
# قواعد إعادة الصياغة
# ==========================================================

RULES_FILE = os.path.join(
    os.path.dirname(__file__),
    "rules_ar.md",
)

with open(
    RULES_FILE,
    "r",
    encoding="utf-8",
) as f:
    SYSTEM_PROMPT = f.read()


SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "{{ALLOWED_CATEGORIES}}",
    json.dumps(
        ALLOWED_CATEGORIES,
        ensure_ascii=False,
    ),
)


# ==========================================================
# Gemini Client
# ==========================================================

def get_client():
    """
    إنشاء عميل Gemini باستخدام المفتاح الحالي.
    """

    global current_key_index

    if not API_KEYS:
        raise RuntimeError(
            "لا توجد مفاتيح Gemini API صالحة في الإعدادات."
        )

    key = API_KEYS[current_key_index]

    return genai.Client(
        api_key=key
    )


def switch_to_next_key():
    """
    الانتقال إلى مفتاح Gemini التالي.

    لا يوجد انتظار هنا؛ الانتقال يتم فورًا
    حتى لا يستهلك الخبر وقتًا طويلًا عند فشل أحد المفاتيح.
    """

    global current_key_index

    if len(API_KEYS) <= 1:
        return False

    current_key_index = (
        current_key_index + 1
    ) % len(API_KEYS)

    print(
        f"🔄 تم التبديل إلى مفتاح Gemini API "
        f"رقم ({current_key_index + 1}/{len(API_KEYS)})"
    )

    return True


# ==========================================================
# فحص التكرار الدلالي
# ==========================================================

def is_semantic_duplicate(
    new_title: str,
    recent_titles: list[str],
) -> bool:
    """
    تم الغاء فحص التكرار.
    """

    return False


# ==========================================================
# معالجة الخبر
# ==========================================================

def process_article(
    raw_text: str,
    source_title: str,
    matched_keyword: str,
) -> dict | None:

    if not raw_text or len(raw_text) < 100:
        print(
            "⚠️ تم تجاوز المقال — النص الأصلي قصير جدًا "
            "أو فارغ (لا يمكن الاعتماد عليه)."
        )

        return None

    user_prompt = (
        f"عنوان الخبر كما ورد من المصدر: {source_title}\n"
        f"الكلمة المفتاحية المرتبطة بالبحث: {matched_keyword}\n\n"
        f"نص الخبر الأصلي الكامل:\n{raw_text}"
    )

    # ======================================================
    # محاولة كل مفتاح مرة واحدة فقط
    # ======================================================

    if not API_KEYS:
        print(
            "❌ لا توجد مفاتيح Gemini API متاحة."
        )

        return None

    max_attempts = len(API_KEYS)

    result = None

    for attempt in range(max_attempts):

        try:
            client = get_client()

            print(
                f"🤖 محاولة معالجة Gemini "
                f"({attempt + 1}/{max_attempts})"
            )

            response = client.models.generate_content(
                model=MODEL,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                ),
            )

            result = json.loads(
                response.text
            )

            # نجاح الطلب.
            break

        except Exception as e:

            error_text = str(e)

            print(
                f"⚠️ فشل استدعاء Gemini "
                f"({attempt + 1}/{max_attempts}): {e}"
            )

            # إذا كان هناك مفتاح آخر،
            # ننتقل إليه فورًا بدون انتظار.
            if attempt < max_attempts - 1:

                switched = switch_to_next_key()

                if switched:
                    continue

            # لا توجد مفاتيح أخرى.
            print(
                "❌ انتهت جميع محاولات Gemini "
                "لهذا الخبر — سيتم تجاوز الخبر "
                "وإعادة محاولته في الدورة القادمة."
            )

            return None

    # ======================================================
    # التأكد من وجود نتيجة
    # ======================================================

    if not result:
        print(
            "❌ لم يتم الحصول على نتيجة من Gemini "
            "— سيتم تجاوز الخبر وإعادة محاولته "
            "في الدورة القادمة."
        )

        return None

    # ======================================================
    # التصنيفات
    # ======================================================

    result["categories"] = [
        c
        for c in result.get(
            "categories",
            [],
        )
        if c in ALLOWED_CATEGORIES
    ]

    if not result["categories"]:
        print(
            "⚠️ لم يتم تحديد تصنيف مناسب للخبر "
            "— سيُنشر بلا تصنيف (غير مصنف)."
        )

    # ======================================================
    # التأكد من وجود العنوان
    # ======================================================

    if not result.get("title"):
        print(
            "⚠️ لم يتم توليد عنوان — "
            "سيتم تجاوز المقال."
        )

        return None

    return result
