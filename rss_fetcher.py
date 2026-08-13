"""
rss_fetcher.py
==============
يجلب الأخبار الرياضية المباشرة من كووورة عبر Google News RSS:
- يتجاوز حظر السيرفرات وCloudflare كليًا.
- فلترة زمنية للأخبار المنشورة خلال آخر 6 ساعات فقط.
- حد أقصى 10 أخبار في الدورة الواحدة.
- منع التكرار القاطع عبر فحص الرابط المباشر في db.
"""

import urllib.request
from datetime import datetime, timezone, timedelta
import feedparser
from config import CONFIG
import db

EXCLUDED_SPORTS_KEYWORDS = [
    "كرة السلة", "السلة", "بطولة السلة", "NBA", "دوري السلة",
    "التنس", "ويمبلدون", "رولان جاروس", "بطولة التنس",
    "فورمولا 1", "فورمولا1", "الفورمولا", "F1",
    "الكريكيت", "الرغبي", "الجولف", "الملاكمة",
    "السباحة", "ألعاب القوى", "الجمباز",
    "Basketball", "Baloncesto", "Tennis", "Tenis",
    "Formula 1", "Fórmula 1", "Cricket", "Rugby", "Golf",
    "Boxing", "Boxeo", "Swimming", "Natación",
    "Baseball", "Béisbol", "NFL", "NHL", "MLB",
]


def _is_recent(entry, max_age_hours: int) -> bool:
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if not published:
        return True
    try:
        published_dt = datetime(*published[:6], tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - published_dt
        return age <= timedelta(hours=max_age_hours)
    except Exception:
        return True


def _is_football_only(title: str) -> bool:
    return not any(bad_word.lower() in title.lower() for bad_word in EXCLUDED_SPORTS_KEYWORDS)


def _fetch_feed_content(url: str) -> bytes:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return response.read()


def fetch_prioritized_news() -> list:
    settings = CONFIG["fetch_settings"]
    max_age = settings.get("max_article_age_hours", 6)
    max_checked = settings.get("max_articles_checked_per_cycle", 10)
    sources = CONFIG.get("rss_sources", [])

    all_news = []
    seen_links = set()

    for source in sources:
        source_name = source.get("name", "مصدر رياضي")
        source_url = source.get("url")

        if not source_url:
            continue

        try:
            raw_data = _fetch_feed_content(source_url)
            feed = feedparser.parse(raw_data)

            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not title or not link or link in seen_links:
                    continue

                if db.is_processed(link):
                    continue

                if not _is_football_only(title):
                    continue

                if not _is_recent(entry, max_age):
                    continue

                published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                published_dt = (
                    datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if published_parsed
                    else datetime.now(timezone.utc)
                )

                seen_links.add(link)
                all_news.append({
                    "title": title,
                    "link": link,
                    "published": entry.get("published", ""),
                    "published_dt": published_dt,
                    "source": source_name,
                    "matched_keyword": source_name,
                })
        except Exception as e:
            print(f"⚠️ فشل جلب الأخبار من المصدر '{source_name}': {e}")
            continue

    all_news.sort(key=lambda x: x["published_dt"], reverse=True)

    final_results = all_news[:max_checked]
    for news in final_results:
        news.pop("published_dt", None)

    return final_results
