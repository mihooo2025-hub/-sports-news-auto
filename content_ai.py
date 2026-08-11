"""
content_ai.py
=============
يرسل نص الخبر الأصلي (بأي لغة) إلى OpenAI ليقوم بـ:
1. الفحص النصي الأولي بواسطة بايثون + المراجعة الذكية عبر OpenAI للتأكد من المعنى.
2. ترجمته للعربية أولًا وإعادة صياغته بأسلوب صحفي رياضي محترف.
3. اقتراح عنوان جذاب واختيار التصنيف المناسب.
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

قواعد اللغة والترجمة:
- إذا كان نص الخبر المُرسل إليك بلغة غير عربية (إنجليزية، إسبانية، إيطالية، أو أي لغة أخرى)،
  ترجمه أولًا ترجمة دقيقة وأمينة للمعنى، ثم أعد صياغته بالعربية وفق القواعد أدناه.
- الناتج النهائي يجب أن يكون بالعربية الفصحى الصحفية دائمًا، بغض النظر عن لغة المصدر.

قواعد الصياغة:
- اعتمد فقط على المعلومات الموجودة حرفيًا في النص المُرسل إليك (بعد ترجمته إن لزم). ممنوع منعًا باتًا استخدام معلومات من ذاكرتك العامة أو التخمين أو افتراض أي تفاصيل غير مذكورة.
- إذا كان النص المُرسل ناقصًا أو غامضًا بخصوص اسم لاعب أو مدرب أو ناديه الحالي، لا تفترض المعلومة؛ اكتب الجملة بصياغة عامة أو احذفها.
- أعد صياغة الخبر بأسلوب صحفي رياضي احترافي، مختصر وسريع ، بدون حشو أو تكرار، يركز على لب الموضوع.
- يفضل أن تكون عدد كلمات الخبر بما يقارب 120 كلمة.
- اكتب بالعربية الفصحى الصحفية الكاملة دائمًا، وأسماء اللاعبين والأندية بالعربية (استخدم الأسماء العربية الشائعة والمعروفة للأندية، مثل "ريال مدريد" وليس "Real Madrid").
-عند الانتقال بين التفاصيل يجب الانتقال بسلاسة بوضع كلمات مناسبة مثل (و، وكما ذكرت ، وايضا وغيرها من الكلمات ) 
'يفضل عدم ذكر أسماء الصحف والمواقع في تفاصيل الخبر 


قواعد العنوان:
- اقترح عنوانًا واحدًا فقط، من 3 إلى 7 كلمات أو أكثر قليلاً.
- جذاب وسريع، يخدم عنصر التشويق والفضول والغموض او تساؤلي ، يلمّح للحدث دون كشف كل التفاصيل.
- بدون خداع، مبني على أقوى زاوية في الخبر لا النتيجة فقط.
- استخدم أفعالًا قوية عند الحاجة مثل: يقترب، يحسم، يضغط، يفاجئ، يترقب، يشعل، يهدد، يكشف، ينعش، يفتح الباب.
- ممنوع ذكر اسم الصحفي أو اسم الموقع المصدر في العنوان.

قواعد التصنيف:
- اختر تصنيفًا واحدًا أو أكثر (بحد أقصى 3) حصريًا من هذه القائمة فقط، دون إضافة أي تصنيف من خارجها بأي شكل:
{json.dumps(ALLOWED_CATEGORIES, ensure_ascii=False)}
- التزم دائمًا باختيار تصنيف النادي الذي يخص الخبر مباشرة إذا كان اسمه مذكورًا في القائمة أعلاه.
- إذا كان الخبر يخص ناديين مختلفين معًا بشكل مباشر (مثل مباراة بين ناديين، أو صفقة انتقال بين ناديين)، اختر تصنيف كل ناد من الناديين معًا في نفس الوقت.
- إذا كان الخبر عن انتقال لاعب أو صفقة، أضف "سوق الانتقالات" إلى جانب تصنيف/تصنيفات الأندية المعنية.
- إذا لم ينتمِ الخبر لأي نادٍ من القائمة، اختر التصنيف الأقرب موضوعيًا من الدوريات أو الأندية المذكورة فيه. لا تترك القائمة فارغة إلا إذا تعذّر تمامًا إيجاد أي تصنيف مناسب.
- ممنوع منعًا باتًا اقتراح أي اسم تصنيف غير موجود حرفيًا في القائمة أعلاه، حتى لو كان قريب الشبه أو بديلاً منطقيًا.

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
