content_ai.py
=============
يرسل نص الخبر الأصلي (بأي لغة) إلى OpenAI ليقوم بـ:
1. الفحص النصي الأولي بواسطة بايثون + المراجعة الذكية عبر OpenAI للتأكد من المعنى.
2. ترجمته للعربية أولًا وإعادة صياغته بأسلوب صحفي رياضي محترف.
3. اقتراح عنوان جذاب وااختيار التصنيف المناسب.
"""

import json
import re
from difflib import SequenceMatcher
from openai import OpenAI
from config import CONFIG

client = OpenAI(api_key=CONFIG["openai"]["api_key"])
MODEL = CONFIG["openai"].get("model", "gpt-4o-mini")

BLOCKED_CATEGORIES = {"اهم الاخبار", "مقالات وتحليلات"}
ALLOWED_CATEGORIES = [c for c in CONFIG["categories"] if c not in BLOCKED_CATEGORIES]

SYSTEM_PROMPT = f"""أنت محرر رياضي محترف متخصص في أخبار كرة القدم لموقع عربي إخباري.
يجب عليك الالتزام الصارم بالقواعد التالية دون استثناء:

قواعد الصياغة وبناء الخبر:
- الالتزام بالنص (عدم التخمين): الاعتماد حصريًا على المعلومات الواردة في النص المرفق. يمنع منعًا باتًا استدعاء معلومات من الذاكرة أو افتراض/تخمين تفاصيل غير مذكورة.
- التعامل مع الغموض: إذا كان النص ناقصًا أو غامضًا بشأن اسم لاعب، مدرب، أو ناديه، تُصاغ الجملة بشكل عام أو تُحذف دون افتراض المعلومة.
- الكتابة باللغة العربية الفصحى وكتابة أسماء اللاعبين والأندية فقط باللغة العربية.
- الأسلوب والتركيز: أسلوب صحفي رياضي احترافي، مختصر وسريع، يركز على لب الموضوع.
- طول الخبر: يفضل أن يكون في حدود 120 كلمة تقريبًا. وإن كان الخبر الأصل قصيرًا، يُعاد صياغته فقط دون اختصاره أكثر.
- السلاسة والتجميع: الانتقال بين التفاصيل بشكل سلس شبيه بالسرد القصصي عبر استخدام أدوات ربط مناسبة مثل (و، وكما ذكرت، وأيضًا...)، مع تجنب وضع الفواصل والنقاط داخل التفاصيل.
- المصادر: يُفضل عدم ذكر أسماء الصحف والمواقع المصدرية داخل تفاصيل الخبر.
- التنسيق: يُخرج نص الخبر بأسلوب HTML بسيط باستخدام فقرات <p>.

قواعد العناوين:
- العدد والطول: اقتراح عنوان واحد فقط، ويتراوح طوله بين 3 إلى 7 كلمات (أو أكثر قليلاً).
- الطابع والجاذبية: عنوان سريع، يجذب الانتباه، ويعتمد على عناصر التشويق والفضول والغموض (أو بصيغة سؤال)، يلمّح للحدث دون كشف كامل التفاصيل.
- الأفعال القوية: استخدام أفعال حركية ومؤثرة مثل: (يقترب، يحسم، يضغط، يفاجئ، يترقب، يشعل، يهدد، يكشف، ينعش، يفتح الباب).
- المحظورات: يمنع ذكر اسم الصحفي أو الموقع المصدر في العنوان.

قواعد التصنيف (Categories):
- القائمة المعتمدة: اختر تصنيفًا واحدًا إلى 3 تصنيفات كحد أقصى حصريًا من هذه القائمة فقط، دون إضافة أي تصنيف من خارجها بأي شكل:
{json.dumps(ALLOWED_CATEGORIES, ensure_ascii=False)}
- تحديد الأندية: التزام تام باختيار تصنيف النادي المذكور بالخبر. وفي حال كان الخبر يخص ناديين (كمباراة أو صفقة)، يُحدد تصنيف كلا الناديين معًا.
- الانتقالات: إضافة تصنيف "سوق الانتقالات" تلقائيًا إلى جانب أندية الخبر إذا كان الموضوع عن صفقة أو انتقال.
- الخيار الأقرب: إذا لم يتبع الخبر لنادٍ محدد، يُختار التصنيف الأقرب موضوعيًا (كالدوري المباشر)، مع تجنب اختراع أي تصنيف من خارج القائمة.

أعد ردك **بصيغة JSON فقط** دون أي نص إضافي أو علامات Markdown، بالمخطط التالي بالضبط:
{{
  "rewritten_content": "نص الخبر المُعاد صياغته بالعربية (HTML بسيط بفقرات <p>)",
  "title": "العنوان المقترح بالعربية",
  "categories": ["تصنيف 1", "تصنيف 2"]
}}
"""


def is_semantic_duplicate(new_title: str, recent_titles: list[str]) -> bool:
    """
    يفحص التكرار مع مراعاة عدم استبعاد الأخبار التي تحمل تفاصيل أو تطورات جديدة.
    """
    if not recent_titles or not new_title:
        return False

    new_title_clean = new_title.strip()
    
    # استخراج الكلمات الأساسية (تجاوز الكلمات القصيرة وأدوات الربط)
    new_words = set(re.findall(r'\w{3,}', new_title_clean.lower()))
    
    suspicious_titles = []

    for old_title in recent_titles:
        old_title_clean = old_title.strip()
        
        # 1. مطابقة نصية شبه متطابقة حرفياً (90% فأكثر)
        ratio = SequenceMatcher(None, new_title_clean, old_title_clean).ratio()
        if ratio >= 0.90:
            print(f"⚠️ تكرار نصي مؤكد بنسبة {int(ratio*100)}%: {new_title_clean}")
            return True

        # 2. فحص تقاطع الكلمات الأساسية
        old_words = set(re.findall(r'\w{3,}', old_title_clean.lower()))
        common_words = new_words.intersection(old_words)
        
        # تحويل للذكاء الاصطناعي فقط إذا كان هناك تقاطع عالي في الكلمات (3 كلمات رئيسية أو نسبة تشابه >= 40%)
        if len(common_words) >= 3 or ratio >= 0.40:
            suspicious_titles.append(old_title_clean)

    # إذا لم يستوفِ شروط الاشتباه، يعتبر خبراً جديداً فوراً
    if not suspicious_titles:
        return False

    suspicious_titles = suspicious_titles[:8]

    prompt = f"""أنت محرر رياضي خبير وميزانك دقيق جداً. مهمتك هي التمييز بين "الخبر المكرر بنفس التفاصيل" و "الخبر الجديد أو التطور البرمجي/التصريحي".

العنوان الجديد المراد فحصه:
"{new_title_clean}"

العناوين المنشورة سابقاً:
{json.dumps(suspicious_titles, ensure_ascii=False)}

قواعد التقييم:
1. يعتبر [مكرر - duplicate: true] فقط إذا كان العنوان الجديد يعيد صياغة نفس الواقعة أو نفس الحدث دون إدخال أي زاوية أو معلومة جديدة.
   - مثال للمكرر: "رودري يرفض ريال مدريد" مقارنة بـ "3 أسباب تجعل رودري يرفض مدريد".

2. يعتبر [غير مكرر - duplicate: false] إذا كان الخبر يحتوي على تحديث، تصريح مختلف، رد فعل جديد، أرقام جديدة، أو واقعة أخرى لنفس اللاعب/النادي.
   - مثال لغير المكرر: "رودري يرفض مدريد" مقارنة بـ "موقف ريال مدريد بعد رفض رودري" أو "رودري يوضح سبب رفضه لمدريد".

أجب بصيغة JSON فقط بالتنسيق التالي:
{{"duplicate": true}} أو {{"duplicate": false}}
"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        res = json.loads(response.choices[0].message.content)
        is_dup = res.get("duplicate", False)
        if is_dup:
            print(f"⚠️ تم حجب الخبر لأنه مكرر تماماً: {new_title_clean}")
        return is_dup
    except Exception as e:
        print(f"⚠️ خطأ في فحص الذكاء الاصطناعي: {e}")
        return False


def process_article(raw_text: str, source_title: str, matched_keyword: str) -> dict | None:
    if not raw_text or len(raw_text) < 100:
        print("⚠️ تم تجاوز المقال — النص الأصلي قصير جدًا أو فارغ (لا يمكن الاعتماد عليه).")
        return None

    user_prompt = (
        f"عنوان الخبر كما ورد من المصدر: {source_title}\n"
        f"الكلمة المفتاحية المرتبطة بالبحث: {matched_keyword}\n\n"
        f"نص الخبر الأصلي الكامل (قد يكون بلغة غير عربية):\n{raw_text}"
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
