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
- عند استنفاد حصة النموذج الحالي يتم تجربة نموذج Gemini احتياطي.
- عند نفاد الحصة لجميع النماذج المتاحة يتم إيقاف الدورة.
- لا توجد فترات انتظار طويلة بين المحاولات.
- إذا فشلت جميع المفاتيح والنماذج لأسباب أخرى، يتم تجاوز الخبر
  ليعاد في الدورة التالية.
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

# نماذج احتياطية عند نفاد حصة النموذج الأساسي.
# يتم تجربتها فقط عند ظهور خطأ quota الخاص بالنموذج.
FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]


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
    API_KEYS = [
        raw_api_key
    ] if raw_api_key else []


current_key_index = 0


# ==========================================================
# التصنيفات
#
# "اهم الاخبار" أصبح مسموحًا الآن، لكن استخدامه مقيّد بشروط
# صارمة محددة داخل rules_ar.md (خبر انتقال رسمي، تحقيق بطولة
# رسمية، إصابة قوية، تعافٍ من إصابة طويلة، أو نتيجة قرعة فقط).
# "مقالات وتحليلات" يبقى ممنوعًا كليًا كما كان.
# ==========================================================

BLOCKED_CATEGORIES = {
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
# حالة نفاد حصة Gemini
# ==========================================================

gemini_quota_exhausted = False


def is_gemini_quota_exhausted() -> bool:
    """
    إرجاع حالة عدم توفر أي نموذج Gemini
    بسبب نفاد الحصة في الدورة الحالية.
    """

    return gemini_quota_exhausted


def is_quota_exhausted_error(
    error_text: str,
) -> bool:
    """
    التحقق مما إذا كان الخطأ متعلقًا بنفاد
    حصة الطلبات اليومية للنموذج/المشروع.
    """

    error_text = error_text.lower()

    quota_indicators = [
        "resource_exhausted",
        "generate_requests_per_day_per_project",
        "quota exceeded",
        "quotaexceeded",
        "free_tier_requests",
        "generaterequestsperdayperprojectpermodel",
    ]

    return any(
        indicator in error_text
        for indicator in quota_indicators
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

    global gemini_quota_exhausted

    if gemini_quota_exhausted:
        return None

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

    if not API_KEYS:
        print(
            "❌ لا توجد مفاتيح Gemini API متاحة."
        )

        return None

    models_to_try = []

    for model_name in [
        MODEL,
        *FALLBACK_MODELS,
    ]:
        if (
            model_name
            and model_name not in models_to_try
        ):
            models_to_try.append(
                model_name
            )

    for model_name in models_to_try:

        print(
            f"🤖 النموذج المستخدم: {model_name}"
        )

        model_quota_exhausted = False

        max_attempts = len(API_KEYS)

        for attempt in range(
            max_attempts
        ):

            try:
                client = get_client()

                print(
                    f"🤖 محاولة معالجة Gemini "
                    f"({attempt + 1}/{max_attempts})"
                )

                response = client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                    ),
                )

                result = json.loads(
                    response.text
                )

                if not result:
                    raise RuntimeError(
                        "لم يتم الحصول على نتيجة من Gemini."
                    )

                # ==================================================
                # التصنيفات
                # ==================================================

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

                # ==================================================
                # التأكد من العنوان
                # ==================================================

                if not result.get("title"):
                    print(
                        "⚠️ لم يتم توليد عنوان — "
                        "سيتم تجاوز المقال."
                    )

                    return None

                return result

            except Exception as e:

                error_text = str(e)

                print(
                    f"⚠️ فشل استدعاء Gemini "
                    f"({attempt + 1}/{max_attempts}) "
                    f"باستخدام {model_name}: {e}"
                )

                # ==================================================
                # نفاد حصة النموذج الحالي
                # ==================================================

                if is_quota_exhausted_error(
                    error_text
                ):
                    model_quota_exhausted = True

                    print(
                        f"⛔ انتهت حصة النموذج {model_name}."
                    )

                    # لا فائدة من تبديل API Key
                    # إذا كانت المفاتيح من نفس المشروع.
                    break

                # ==================================================
                # خطأ عادي — تجربة المفتاح التالي
                # ==================================================

                if attempt < max_attempts - 1:

                    switched = switch_to_next_key()

                    if switched:
                        continue

                break

        # ======================================================
        # الانتقال إلى نموذج احتياطي
        # ======================================================

        if model_quota_exhausted:

            if model_name != models_to_try[-1]:
                print(
                    "🔄 الانتقال إلى نموذج Gemini احتياطي..."
                )
                continue

            gemini_quota_exhausted = True

            print(
                "⛔ انتهت حصص جميع نماذج Gemini المتاحة."
            )

            print(
                "⏹️ سيتم إيقاف معالجة الأخبار لبقية الدورة الحالية."
            )

            return None

    print(
        "❌ فشلت جميع محاولات Gemini "
        "لهذا الخبر — سيتم تجاوز الخبر "
        "وإعادة محاولته في الدورة القادمة."
    )

    return None
