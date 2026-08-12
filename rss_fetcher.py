"""
rss_fetcher.py
==============
يجلب أخبار كرة القدم من Google News RSS بلغتين أو أكثر (عربية دائمًا + مصادر
عالمية اختيارية مثل Marca وSport وESPN حسب locales المحددة في config.json)، حسب:
- كلمات مفتاحية بالأولوية (ريال مدريد وبرشلونة أولًا، ثم الدوريات الكبرى، ثم العام)
- فرز أحدث الأخبار زمنياً أولاً لكل مستوى أولوية
- فلترة زمنية: آخر 12 ساعة فقط
- استبعاد صارم لأي رياضة غير كرة القدم (بعدة لغات)
- سقف أقصى لعدد الأخبار المفحوصة في كل دورة (افتراضيًا 100)
"""

import feedparser
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

from config import CONFIG

# كلمات تستبعد الخبر فورًا إذا وُجدت في العنوان (رياضات أخرى، بعدة لغات)
EXCLUDED_SPORTS_KEYWORDS = [
    # عربي
    "كرة السلة", "السلة", "بطولة السلة", "NBA", "دوري السلة",
    "التنس", "ويمبلدون", "رولان جاروس", "بطولة التنس",
    "فورمولا 1", "فورمولا1", "الفورمولا", "F1",
    "الكريكيت", "الرغبي", "الجولف", "الملاكمة",
    "السباحة", "ألعاب القوى", "الجمباز",
    # إنجليزي/إسباني/عام
    "Basketball", "Baloncesto", "Tennis", "Tenis",
    "Formula 1", "Fórmula 1", "Cricket", "Rugby", "Golf",
    "Boxing", "Boxeo", "Swimming", "Natación",
    "Baseball", "Béisbol", "NFL", "NHL", "MLB",
]

# مؤهل "كرة القدم" حسب لغة البحث، يُستخدم فقط للكلمات العامة/الدوريات
FOOTBALL_QUALIFIER_BY_LANGUAGE = {
    "ar": "كرة القدم",
    "en": "football",
    "es": "fútbol",
    "it": "calcio",
    "de": "Fußball",
    "fr": "football",
}


def _build_gnews_url(query: str, language: str, country: str, add_football_qualifier: bool = False) -> str:
    qualifier = FOOTBALL_QUALIFIER_BY_LANGUAGE.get(language, "football")
    final_query = query
    if add_football_qualifier and qualifier not in query:
        final_query = f"{query} {qualifier}"

    encoded_query = quote(final_query)
    return (
        f"https://news.google.com/rss/search?q={encoded_query}"
        f"&hl={language}&gl={country}&ceid={country}:{language}"
    )


def _is_recent(entry, max_age_hours: int) -> bool:
    published = entry.get("published_parsed")
    if not published:
        return False
    published_dt = datetime(*published[:6], tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - published_dt
    return age <= timedelta(hours=max_age_hours)


def _is_football_only(title: str) -> bool:
    return not any(bad_word.lower() in title.lower() for bad_word in EXCLUDED_SPORTS_KEYWORDS)


def fetch_news_for_keyword(
    keyword: str, max_age_hours: int, language: str, country: str, add_football_qualifier: bool = False
) -> list:
    url = _build_gnews_url(keyword, language, country, add_football_qualifier)
    feed = feedparser.parse(url)

    results = []
    for entry in feed.entries:
        title = entry.get("title", "")
        link = entry.get("link", "")
        if not title or not link:
            continue
        if not _is_football_only(title):
            continue
        if not _is_recent(entry, max_age_hours):
            continue

        published_parsed = entry.get("published_parsed")
        published_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc) if published_parsed else datetime.min.replace(tzinfo=timezone.utc)

        results.append({
            "title": title,
            "link": link,
            "published": entry.get("published", ""),
            "published_dt": published_dt,  # أضيف لاستخدامه في الفرز الزمني
            "source": entry.get("source", {}).get("title", "") if entry.get("source") else "",
            "matched_keyword": keyword,
            "search_language": language,
        })
    return results


def _build_locales_list() -> list:
    """
    يبني قائمة اللغات/الدول للبحث: العربية دائمًا أولًا، ثم المصادر العالمية
    إن كانت مفعّلة في الإعدادات.
    """
    settings = CONFIG["fetch_settings"]
    locales = [{"language": settings["language"], "country": settings["country"]}]

    global_cfg = CONFIG.get("global_sources", {})
    if global_cfg.get("enabled"):
        locales.extend(global_cfg.get("locales", []))

    return locales


def fetch_prioritized_news() -> list:
    """
    يجلب الأخبار بالترتيب الصارم للأولويات:
    1. أولوية الكلمات المفتاحية (الأندية أولاً ثم الدوريات ثم العام).
    2. فرز المقالات داخل كل مستوى لترتيب الأحدث زمنياً أولاً (Recency).
    3. أولوية المصادر (العربية أولاً ثم المصادر العالمية).
    """
    settings = CONFIG["fetch_settings"]
    max_age = settings["max_article_age_hours"]
    max_checked = settings.get("max_articles_checked_per_cycle", 100)

    keyword_groups = [
        # المجموعة الأولى: الأندية والكلمات عالية الأولوية
        ([(kw, False) for kw in CONFIG["priority_keywords"]]),
        # المجموعة الثانية: الدوريات والبطولات
        ([(kw, True) for kw in CONFIG["league_keywords"]]),
        # المجموعة الثالثة: الكلمات العامة
        ([(kw, True) for kw in CONFIG["general_keywords"]]),
    ]

    locales = _build_locales_list()
    all_news = []
    seen_links = set()

    # المرور على مجموعات الكلمات المفتاحية حسب الترتيب العالي
    for group in keyword_groups:
        group_items = []

        for locale in locales:
            lang = locale["language"]
            country = locale["country"]

            for keyword, add_qualifier in group:
                try:
                    items = fetch_news_for_keyword(keyword, max_age, lang, country, add_qualifier)
                    for item in items:
                        if item["link"] not in seen_links:
                            seen_links.add(item["link"])
                            group_items.append(item)
                except Exception as e:
                    print(f"⚠️ فشل جلب أخبار الكلمة '{keyword}' ({lang}-{country}): {e}")
                    continue

        # فرز أخبار هذه المجموعة زمنياً (الأحدث أولاً) قبل إضافتها للقائمة الرئيسية
        group_items.sort(key=lambda x: x["published_dt"], reverse=True)
        all_news.extend(group_items)

        # التوقف إذا وصلنا للسقف المطلوب مع الحفاظ على المقالات الأكثر أولوية
        if len(all_news) >= max_checked:
            break

    # تنظيف حقل تاريخ المقارنة المؤقت قبل إرجاع البيانات
    final_results = all_news[:max_checked]
    for news in final_results:
        news.pop("published_dt", None)

    return final_results
