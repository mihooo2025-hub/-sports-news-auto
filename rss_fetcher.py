"""
rss_fetcher.py
==============
يجلب أخبار كرة القدم من Google News RSS بلغتين أو أكثر (عربية دائمًا + مصادر
عالمية اختيارية مثل Marca وSport وESPN حسب locales المحددة في config.json)، حسب:
- كلمات مفتاحية بالأولوية (ريال مدريد وبرشلونة أولًا، ثم الدوريات الكبرى، ثم العام)
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
# التي قد تشترك فيها رياضات أخرى (وليس لأسماء الأندية الواضحة أصلًا)
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
        results.append({
            "title": title,
            "link": link,
            "published": entry.get("published", ""),
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
    يجلب الأخبار بالترتيب: العربية أولًا (أولوية قصوى -> دوريات -> عام)،
    ثم المصادر العالمية بنفس الترتيب، مع الحفاظ على هذا الترتيب في القائمة
    المُعادة (المهم أولاً)، وسقف أقصى لعدد الأخبار المفحوصة الكلي.
    """
    settings = CONFIG["fetch_settings"]
    max_age = settings["max_article_age_hours"]
    max_checked = settings.get("max_articles_checked_per_cycle", 100)

    ordered_keywords = (
        # أسماء الأندية واضحة أصلًا ولا تحتاج مؤهل كرة القدم
        [(kw, False) for kw in CONFIG["priority_keywords"]]
        # أسماء الدوريات تحتاج المؤهل للتمييز عن رياضات أخرى
        + [(kw, True) for kw in CONFIG["league_keywords"]]
        # الكلمات العامة (المؤهل يُضاف فقط إن لم تكن موجودة أصلًا في النص)
        + [(kw, True) for kw in CONFIG["general_keywords"]]
    )

    locales = _build_locales_list()

    all_news = []
    seen_links = set()

    for locale in locales:
        lang = locale["language"]
        country = locale["country"]

        for keyword, add_qualifier in ordered_keywords:
            if len(all_news) >= max_checked:
                return all_news[:max_checked]

            try:
                items = fetch_news_for_keyword(keyword, max_age, lang, country, add_qualifier)
            except Exception as e:
                print(f"⚠️ فشل جلب أخبار الكلمة '{keyword}' ({lang}-{country}): {e}")
                continue

            for item in items:
                if item["link"] in seen_links:
                    continue
                seen_links.add(item["link"])
                all_news.append(item)
                if len(all_news) >= max_checked:
                    break

    return all_news[:max_checked]
