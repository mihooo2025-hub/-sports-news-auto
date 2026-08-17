"""
rss_fetcher.py
==============
يجلب جميع الأخبار الرياضية المتاحة كليًا من كووورة عبر Google News RSS:
- يتجاوز حظر السيرفرات وCloudflare كليًا.
- يجلب كل الأخبار المنشورة خلال آخر 6 ساعات دون تحديد حد أقصى للعدد.
- جلب الأخبار حسب الأحدث زمنياً.
- منع التكرار القاطع عبر فحص الرابط المباشر وعنوان الخبر في db.
"""

import urllib.request
from datetime import datetime, timezone, timedelta
import feedparser
from config import CONFIG
import db


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


def _clean_title(title: str) -> str:
    """تنظيف العنوان من ملحق اسم الموقع القادم من Google News (مثل - كووورة)"""
    for tag in [" - كووورة", " - كوووره", " - Kooora", " - kooora"]:
        if title.endswith(tag):
            title = title[:-len(tag)].strip()
    return title


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
    sources = CONFIG.get("rss_sources", [])

    all_news = []
    seen_links = set()
    seen_titles = set()

    for source in sources:
        source_name = source.get("name", "مصدر رياضي")
        source_url = source.get("url")

        if not source_url:
            continue

        try:
            raw_data = _fetch_feed_content(source_url)
            feed = feedparser.parse(raw_data)

            for entry in feed.entries:
                raw_title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not raw_title or not link or link in seen_links:
                    continue

                # إزالة كلمة كووورة من نهايات العناوين تلقائياً قبل معالجتها
                clean_title = _clean_title(raw_title)
                normalized_title = clean_title.strip().lower()

                if normalized_title in seen_titles:
                    continue

                # فحص التكرار في قاعدة البيانات بالرابط والعنوان المنظف
                if db.is_processed(link, clean_title):
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
                seen_titles.add(normalized_title)
                all_news.append({
                    "title": clean_title,
                    "link": link,
                    "published": entry.get("published", ""),
                    "published_dt": published_dt,
                    "source": source_name,
                    "matched_keyword": source_name,
                })
        except Exception as e:
            print(f"⚠️ فشل جلب الأخبار من المصدر '{source_name}': {e}")
            continue

    # الترتيب حسب الوقت (الأحدث أولاً)
    all_news.sort(key=lambda x: x["published_dt"], reverse=True)

    # إزالة التاريخ المساعد وإعادة كافة الأخبار المتاحة بدون تقييد للعدد
    for news in all_news:
        news.pop("published_dt", None)

    return all_news
