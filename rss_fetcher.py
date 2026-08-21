"""
rss_fetcher.py
==============
يجلب أخبار كووورة مباشرة، مع Google News RSS كخطة احتياطية.
- المصدر الأساسي: صفحات أخبار كووورة.
- يفحص عدة صفحات من الأحدث إلى الأقدم.
- يحتفظ بفلترة آخر 5 ساعات.
- يمنع التكرار عبر الرابط والعنوان في قاعدة البيانات.
"""

import html
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import feedparser
from bs4 import BeautifulSoup

from config import CONFIG
import db


KOOORA_BASE = "https://www.kooora.com"
KOOORA_NEWS = f"{KOOORA_BASE}/news"


def _is_recent(entry, max_age_hours: int) -> bool:
    published = entry.get("published_parsed") or entry.get("updated_parsed")

    if not published:
        return True

    try:
        published_dt = datetime(*published[:6], tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - published_dt <= timedelta(
            hours=max_age_hours
        )
    except Exception:
        return True


def _clean_title(title: str) -> str:
    for tag in [
        " - كووورة",
        " - كوووره",
        " - Kooora",
        " - kooora",
    ]:
        if title.endswith(tag):
            title = title[:-len(tag)].strip()

    return re.sub(r"\s+", " ", title).strip()


def _fetch_url(url: str, timeout: int = 20) -> bytes:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "ar,en-US;q=0.8,en;q=0.6",
    }

    request = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _article_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)

        if parsed.netloc.lower() not in {
            "kooora.com",
            "www.kooora.com",
        }:
            return False

        path = parsed.path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        if len(parts) < 2:
            return False

        last = parts[-1]

        # صفحات الأخبار نفسها ليست مقالات.
        if path in {"/news", "/news/"}:
            return False

        if re.fullmatch(r"news/\d+", path.lstrip("/")):
            return False

        if re.fullmatch(r"\d+", last):
            return True

        if last.startswith("blt"):
            return True

        return False

    except Exception:
        return False


def _parse_datetime(value: str):
    if not value:
        return None

    value = value.strip()

    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(timezone.utc)

    except Exception:
        return None


def _extract_entry_time(anchor):
    """
    يحاول العثور على وقت نشر الخبر من عناصر <time>
    القريبة من رابط الخبر.
    """

    node = anchor

    for _ in range(7):
        if node is None:
            break

        time_tag = node.find("time")

        if time_tag:
            value = (
                time_tag.get("datetime")
                or time_tag.get("content")
                or time_tag.get_text(" ", strip=True)
            )

            parsed = _parse_datetime(value)

            if parsed:
                return parsed

        node = node.parent

    return None


def _parse_kooora_page(raw_data: bytes, source_name: str) -> list:
    soup = BeautifulSoup(raw_data, "html.parser")

    results = []
    seen = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()

        if not href:
            continue

        link = urllib.parse.urljoin(KOOORA_BASE, href)

        if not _article_url(link):
            continue

        link = link.split("#", 1)[0]

        if link in seen:
            continue

        title = anchor.get_text(" ", strip=True)
        title = html.unescape(title)
        title = _clean_title(title)

        if len(title) < 10:
            continue

        seen.add(link)

        published_dt = _extract_entry_time(anchor)

        results.append({
            "title": title,
            "link": link,
            "published": published_dt.isoformat() if published_dt else "",
            "published_dt": published_dt or datetime.now(timezone.utc),
            "source": source_name,
            "matched_keyword": source_name,
        })

    return results


def _fetch_kooora_direct(max_age: int, pages: int) -> list:
    all_news = []
    seen_links = set()
    seen_titles = set()

    for page in range(1, pages + 1):
        url = KOOORA_NEWS if page == 1 else f"{KOOORA_NEWS}/{page}"

        try:
            raw_data = _fetch_url(url)
            page_news = _parse_kooora_page(
                raw_data,
                "Kooora",
            )

            for item in page_news:
                link = item["link"]
                title = item["title"]

                if link in seen_links:
                    continue

                # مطابقة العنوان 100% بعد تنظيف المسافات فقط.
                # لا يوجد تشابه تقريبي حتى لا يتم فقد أخبار مختلفة.
                normalized_title = re.sub(
                    r"\s+",
                    " ",
                    title.strip(),
                ).lower()

                if normalized_title in seen_titles:
                    continue

                entry = {
                    "published_parsed": (
                        item["published_dt"].timetuple()
                        if item.get("published_dt")
                        else None
                    )
                }

                if not _is_recent(entry, max_age):
                    continue

                if db.is_processed(link, title):
                    continue

                seen_links.add(link)
                seen_titles.add(normalized_title)
                all_news.append(item)

        except Exception as e:
            print(
                f"⚠️ تعذر جلب صفحة كووورة رقم {page}: {e}"
            )

    return all_news


def _fetch_google_news_fallback(max_age: int) -> list:
    sources = CONFIG.get("rss_sources", [])

    all_news = []
    seen_links = set()
    seen_titles = set()

    for source in sources:
        source_name = source.get(
            "name",
            "Google News Fallback",
        )
        source_url = source.get("url")

        if not source_url:
            continue

        try:
            raw_data = _fetch_url(source_url)
            feed = feedparser.parse(raw_data)

            for entry in feed.entries:
                raw_title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()

                if not raw_title or not link:
                    continue

                clean_title = _clean_title(raw_title)

                # مطابقة العنوان 100% بعد التنظيف المحافظ.
                normalized_title = re.sub(
                    r"\s+",
                    " ",
                    clean_title.strip(),
                ).lower()

                if link in seen_links:
                    continue

                if normalized_title in seen_titles:
                    continue

                if db.is_processed(link, clean_title):
                    continue

                if not _is_recent(entry, max_age):
                    continue

                published_parsed = (
                    entry.get("published_parsed")
                    or entry.get("updated_parsed")
                )

                published_dt = (
                    datetime(
                        *published_parsed[:6],
                        tzinfo=timezone.utc,
                    )
                    if published_parsed
                    else datetime.now(timezone.utc)
                )

                seen_links.add(link)
                seen_titles.add(normalized_title)

                all_news.append({
                    "title": clean_title,
                    "link": link,
                    "published": entry.get(
                        "published",
                        "",
                    ),
                    "published_dt": published_dt,
                    "source": source_name,
                    "matched_keyword": "Kooora",
                })

        except Exception as e:
            print(
                f"⚠️ فشل Google News fallback: {e}"
            )

    return all_news


def fetch_prioritized_news() -> list:
    settings = CONFIG.get(
        "fetch_settings",
        {},
    )

    # فحص الأخبار يقتصر على آخر 5 ساعات.
    max_age = 5

    pages = settings.get(
        "kooora_pages",
        3,
    )

    print("🔎 محاولة جلب الأخبار مباشرة من كووورة...")
    print(
        "⏱️ سيتم فحص الأخبار المنشورة خلال آخر 5 ساعات فقط."
    )

    direct_news = _fetch_kooora_direct(
        max_age,
        pages,
    )

    # إذا نجح الجلب المباشر، نعتمد عليه.
    if direct_news:
        all_news = direct_news
        print(
            f"✅ تم العثور على {len(direct_news)} "
            "خبرًا من كووورة مباشرة."
        )
    else:
        print(
            "⚠️ لم يتم الحصول على أخبار مباشرة من كووورة."
        )
        print(
            "🔄 الانتقال إلى Google News كخطة احتياطية..."
        )

        all_news = _fetch_google_news_fallback(
            max_age,
        )

    all_news.sort(
        key=lambda x: x["published_dt"],
        reverse=True,
    )

    for news in all_news:
        news.pop("published_dt", None)

    return all_news
