"""
rss_fetcher.py
==============
يجلب أخبار كووورة مباشرة، مع Google News RSS كخطة احتياطية.

- المصدر الأساسي: صفحات أخبار كووورة.
- يفحص عدة صفحات من الأحدث إلى الأقدم.
- يلتزم بآخر 3 ساعات محسوبة من وقت بداية دورة التشغيل.
- يمنع الأخبار التي لا يمكن تحديد وقت نشرها بدقة من الدخول إلى المعالجة.
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
# رابط صفحة الأخبار الصحيح على كووورة (المسار العربي "/أخبار").
# الرابط القديم "/news" غير صحيح ولا يعيد أي مقالات فعلية،
# وهو السبب الرئيسي في ضعف عدد الأخبار المجلوبة سابقًا.
# يجب ترميز الجزء العربي (URL-encode) وإلا يفشل urllib بخطأ
# "'ascii' codec can't encode characters" عند بناء الطلب.
KOOORA_NEWS = f"{KOOORA_BASE}/{urllib.parse.quote('أخبار')}"

KOOORA_TIMEZONE = timezone(
    timedelta(hours=3)
)

ARABIC_MONTHS = {
    "يناير": 1,
    "فبراير": 2,
    "مارس": 3,
    "أبريل": 4,
    "ابريل": 4,
    "مايو": 5,
    "يونيو": 6,
    "يوليو": 7,
    "أغسطس": 8,
    "اغسطس": 8,
    "سبتمبر": 9,
    "أكتوبر": 10,
    "اكتوبر": 10,
    "نوفمبر": 11,
    "ديسمبر": 12,
}

ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "01234567890123456789",
)


def _is_recent(
    published_dt,
    cycle_start: datetime,
    max_age_hours: int,
) -> bool:
    """
    التأكد من أن الخبر نُشر ضمن النافذة الزمنية المطلوبة.

    الحد الأعلى:
        وقت بداية الدورة.

    الحد الأدنى:
        وقت بداية الدورة ناقص 3 ساعات.

    الأخبار التي لا تملك وقت نشر معروفًا لا تمر.
    """

    if not published_dt:
        return False

    try:
        if published_dt.tzinfo is None:
            published_dt = published_dt.replace(
                tzinfo=timezone.utc
            )

        published_dt = published_dt.astimezone(
            timezone.utc
        )

        cutoff = (
            cycle_start
            - timedelta(hours=max_age_hours)
        )

        return (
            cutoff
            <= published_dt
            <= cycle_start
        )

    except Exception:
        return False


def _clean_title(title: str) -> str:
    for tag in [
        " - كووورة",
        " - كوووره",
        " - Kooora",
        " - kooora",
    ]:
        if title.endswith(tag):
            title = title[:-len(tag)].strip()

    return re.sub(
        r"\s+",
        " ",
        title,
    ).strip()


def _fetch_url(
    url: str,
    timeout: int = 20,
) -> bytes:
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

    request = urllib.request.Request(
        url,
        headers=headers,
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:
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
        parts = [
            p
            for p in path.split("/")
            if p
        ]

        if len(parts) < 2:
            return False

        last = parts[-1]

        # صفحات الأخبار نفسها ليست مقالات.
        if path in {"/news", "/news/"}:
            return False

        if re.fullmatch(
            r"news/\d+",
            path.lstrip("/"),
        ):
            return False

        if re.fullmatch(
            r"\d+",
            last,
        ):
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
        value = value.replace(
            "Z",
            "+00:00",
        )

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def _parse_visible_datetime(text: str):
    """
    استخراج التاريخ والوقت من النص الظاهر في بطاقة الخبر.

    يدعم مثلًا:
        09:46 20 أغسطس 2026
        20 أغسطس 2026 09:46
    """

    if not text:
        return None

    text = (
        str(text)
        .translate(ARABIC_DIGITS)
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    month_pattern = (
        r"(يناير|فبراير|مارس|أبريل|ابريل|مايو|"
        r"يونيو|يوليو|أغسطس|اغسطس|سبتمبر|"
        r"أكتوبر|اكتوبر|نوفمبر|ديسمبر)"
    )

    patterns = [
        rf"(\d{{1,2}}):(\d{{2}})\s+"
        rf"(\d{{1,2}})\s+{month_pattern}\s+(\d{{4}})",

        rf"(\d{{1,2}})\s+{month_pattern}\s+"
        rf"(\d{{4}})\s+(\d{{1,2}}):(\d{{2}})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
        )

        if not match:
            continue

        try:
            groups = match.groups()

            # الشكل الأول:
            # HH:MM DD MONTH YYYY
            if len(groups) == 5:
                if ":" in match.group(0).split()[0]:
                    hour = int(groups[0])
                    minute = int(groups[1])
                    day = int(groups[2])
                    month_name = groups[3]
                    year = int(groups[4])
                else:
                    day = int(groups[0])
                    month_name = groups[1]
                    year = int(groups[2])
                    hour = int(groups[3])
                    minute = int(groups[4])

            else:
                continue

            month = ARABIC_MONTHS.get(
                month_name
            )

            if not month:
                continue

            naive_dt = datetime(
                year,
                month,
                day,
                hour,
                minute,
            )

            return naive_dt.replace(
                tzinfo=KOOORA_TIMEZONE
            ).astimezone(
                timezone.utc
            )

        except Exception:
            continue

    return None


def _extract_entry_time(anchor):
    """
    يحاول العثور على وقت نشر الخبر من:
    1. عناصر <time>.
    2. النص الظاهر في الرابط.
    3. النص الظاهر في العناصر الأب القريبة.
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
                or time_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            parsed = _parse_datetime(value)

            if parsed:
                return parsed

        node = node.parent

    anchor_text = anchor.get_text(
        " ",
        strip=True,
    )

    parsed = _parse_visible_datetime(
        anchor_text
    )

    if parsed:
        return parsed

    node = anchor

    for _ in range(5):
        if node is None:
            break

        text = node.get_text(
            " ",
            strip=True,
        )

        parsed = _parse_visible_datetime(
            text
        )

        if parsed:
            return parsed

        node = node.parent

    return None


def _parse_kooora_page(
    raw_data: bytes,
    source_name: str,
) -> list:
    soup = BeautifulSoup(
        raw_data,
        "html.parser",
    )

    results = []
    seen = set()

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        href = anchor.get(
            "href",
            "",
        ).strip()

        if not href:
            continue

        link = urllib.parse.urljoin(
            KOOORA_BASE,
            href,
        )

        if not _article_url(link):
            continue

        link = link.split(
            "#",
            1,
        )[0]

        if link in seen:
            continue

        title = anchor.get_text(
            " ",
            strip=True,
        )

        title = html.unescape(
            title
        )

        title = _clean_title(
            title
        )

        if len(title) < 10:
            continue

        published_dt = _extract_entry_time(
            anchor
        )

        # لا نحول الخبر الذي لا نعرف
        # وقت نشره إلى "الآن".
        if not published_dt:
            continue

        seen.add(link)

        results.append(
            {
                "title": title,
                "link": link,
                "published": published_dt.isoformat(),
                "published_dt": published_dt,
                "source": source_name,
                "matched_keyword": source_name,
            }
        )

    return results


def _fetch_kooora_direct(
    cycle_start: datetime,
    max_age: int,
    pages: int,
) -> list:
    all_news = []
    seen_links = set()
    seen_titles = set()

    for page in range(
        1,
        pages + 1,
    ):
        url = (
            KOOORA_NEWS
            if page == 1
            else f"{KOOORA_NEWS}/{page}"
        )

        try:
            raw_data = _fetch_url(
                url
            )

            page_news = _parse_kooora_page(
                raw_data,
                "Kooora",
            )

            # تسجيل تشخيصي: يوضح هل المشكلة في التحليل
            # (لا توجد روابط مقالات) أم في نافذة التوقيت
            # (روابط موجودة لكن قديمة عن النافذة المطلوبة).
            print(
                f"🧪 تشخيص صفحة {page}: "
                f"حجم الاستجابة {len(raw_data)} بايت، "
                f"عدد المقالات المكتشفة (بغض النظر عن التوقيت): "
                f"{len(page_news)}"
            )

            if page_news:
                dates_found = [
                    item["published_dt"]
                    for item in page_news
                    if item.get("published_dt")
                ]

                if dates_found:
                    print(
                        f"🧪 أحدث تاريخ مكتشف: "
                        f"{max(dates_found).isoformat()}"
                    )

                    print(
                        f"🧪 أقدم تاريخ مكتشف: "
                        f"{min(dates_found).isoformat()}"
                    )
            else:
                print(
                    "🧪 لم يتم العثور على أي رابط مقال "
                    "مطابق للشروط في هذه الصفحة — "
                    "قد تكون بنية الصفحة تغيّرت أو "
                    "تحتاج JavaScript rendering."
                )

            for item in page_news:
                link = item["link"]
                title = item["title"]

                if link in seen_links:
                    continue

                normalized_title = re.sub(
                    r"\s+",
                    " ",
                    title.strip(),
                ).lower()

                if normalized_title in seen_titles:
                    continue

                if not _is_recent(
                    item.get("published_dt"),
                    cycle_start,
                    max_age,
                ):
                    continue

                if db.is_processed(
                    link,
                    title,
                ):
                    continue

                seen_links.add(link)
                seen_titles.add(
                    normalized_title
                )

                all_news.append(item)

        except Exception as e:
            print(
                f"⚠️ تعذر جلب صفحة كووورة رقم {page}: {e}"
            )

    return all_news


def _fetch_google_news_fallback(
    cycle_start: datetime,
    max_age: int,
) -> list:
    sources = CONFIG.get(
        "rss_sources",
        [],
    )

    all_news = []
    seen_links = set()
    seen_titles = set()

    for source in sources:
        source_name = source.get(
            "name",
            "Google News Fallback",
        )

        source_url = source.get(
            "url"
        )

        if not source_url:
            continue

        try:
            raw_data = _fetch_url(
                source_url
            )

            feed = feedparser.parse(
                raw_data
            )

            for entry in feed.entries:
                raw_title = entry.get(
                    "title",
                    "",
                ).strip()

                link = entry.get(
                    "link",
                    "",
                ).strip()

                if not raw_title or not link:
                    continue

                clean_title = _clean_title(
                    raw_title
                )

                normalized_title = re.sub(
                    r"\s+",
                    " ",
                    clean_title.strip(),
                ).lower()

                if link in seen_links:
                    continue

                if normalized_title in seen_titles:
                    continue

                if db.is_processed(
                    link,
                    clean_title,
                ):
                    continue

                published_parsed = (
                    entry.get("published_parsed")
                    or entry.get("updated_parsed")
                )

                if not published_parsed:
                    continue

                try:
                    published_dt = datetime(
                        *published_parsed[:6],
                        tzinfo=timezone.utc,
                    )
                except Exception:
                    continue

                if not _is_recent(
                    published_dt,
                    cycle_start,
                    max_age,
                ):
                    continue

                seen_links.add(link)
                seen_titles.add(
                    normalized_title
                )

                all_news.append(
                    {
                        "title": clean_title,
                        "link": link,
                        "published": entry.get(
                            "published",
                            "",
                        ),
                        "published_dt": published_dt,
                        "source": source_name,
                        "matched_keyword": "Kooora",
                    }
                )

        except Exception as e:
            print(
                f"⚠️ فشل Google News fallback: {e}"
            )

    return all_news


def fetch_prioritized_news(
    cycle_start: datetime | None = None,
) -> list:
    settings = CONFIG.get(
        "fetch_settings",
        {},
    )

    max_age = 3

    if cycle_start is None:
        cycle_start = datetime.now(
            timezone.utc
        )

    if cycle_start.tzinfo is None:
        cycle_start = cycle_start.replace(
            tzinfo=timezone.utc
        )

    cycle_start = cycle_start.astimezone(
        timezone.utc
    )

    cutoff = (
        cycle_start
        - timedelta(hours=max_age)
    )

    pages = settings.get(
        "kooora_pages",
        3,
    )

    print(
        "🔎 محاولة جلب الأخبار مباشرة من كووورة..."
    )

    print(
        "⏱️ نافذة الفحص: آخر 3 ساعات "
        "من وقت بدء الدورة."
    )

    print(
        f"🕐 بداية الدورة: "
        f"{cycle_start.isoformat()}"
    )

    print(
        f"🕐 بداية النافذة: "
        f"{cutoff.isoformat()}"
    )

    direct_news = _fetch_kooora_direct(
        cycle_start,
        max_age,
        pages,
    )

    if direct_news:
        all_news = direct_news

        print(
            f"✅ تم العثور على {len(direct_news)} "
            "خبرًا من كووورة مباشرة ضمن النافذة الزمنية."
        )

    else:
        print(
            "⚠️ لم يتم الحصول على أخبار مباشرة "
            "ضمن النافذة الزمنية من كووورة."
        )

        print(
            "🔄 الانتقال إلى Google News "
            "كخطة احتياطية..."
        )

        all_news = _fetch_google_news_fallback(
            cycle_start,
            max_age,
        )

    all_news = [
        news
        for news in all_news
        if _is_recent(
            news.get("published_dt"),
            cycle_start,
            max_age,
        )
    ]

    all_news.sort(
        key=lambda x: x["published_dt"],
        reverse=True,
    )

    for news in all_news:
        news.pop(
            "published_dt",
            None,
        )

    return all_news
