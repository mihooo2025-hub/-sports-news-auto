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
KOOORA_NEWS = f"{KOOORA_BASE}/news"

# كووورة تستخدم هذا المسار فعليًا للصفحات التالية:
# https://www.kooora.com/أخبار/2
# https://www.kooora.com/أخبار/3
KOOORA_PAGINATION_SEGMENT = "أخبار"

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
        "Referer": "https://www.kooora.com/",
        "Connection": "keep-alive",
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


NON_ARTICLE_SEGMENTS = {
    "live",
    "videos",
    "video",
    "photos",
    "photo",
    "gallery",
    "tags",
    "tag",
    "category",
    "categories",
    "teams",
    "team",
    "players",
    "player",
    "standings",
    "fixtures",
    "results",
    "live-scores",
    "livescore",
    "livescores",
}


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

        # صفحات القوائم نفسها ليست مقالات.
        if path in {
            "/news",
            "/news/",
            "/أخبار",
            "/أخبار/",
        }:
            return False

        # صفحات الترقيم ليست مقالات.
        if re.fullmatch(
            r"news/\d+",
            path.lstrip("/"),
        ):
            return False

        if re.fullmatch(
            r"أخبار/\d+",
            path.lstrip("/"),
        ):
            return False

        # استبعاد صفحات ليست مقالات إخبارية
        # (فيديو، بث مباشر، تصنيفات...)
        if any(
            part.lower() in NON_ARTICLE_SEGMENTS
            for part in parts
        ):
            return False

        last = parts[-1]

        # رابط ينتهي برقم صافٍ، مثل:
        # /news/123456
        if re.fullmatch(
            r"\d+",
            last,
        ):
            return True

        # رابط يبدأ بمعرف من نوع blt
        if last.lower().startswith("blt"):
            return True

        # رابط بصيغة:
        # معرف-عنوان-الخبر
        # أو عنوان-الخبر-معرف
        if re.search(
            r"(?:^|-)\d{4,}(?:-|$)",
            last,
        ):
            return True

        # رابط بصيغة:
        # /news/<رقم>/عنوان-الخبر
        if any(
            re.fullmatch(
                r"\d{4,}",
                part,
            )
            for part in parts[:-1]
        ):
            return True

        return False

    except Exception:
        return False


def _parse_datetime(value: str):
    if not value:
        return None

    value = (
        str(value)
        .strip()
    )

    if not value:
        return None

    try:
        value = value.replace(
            "Z",
            "+00:00",
        )

        dt = datetime.fromisoformat(
            value
        )

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

    يدعم:
        09:46 20 أغسطس 2026
        20 أغسطس 2026 09:46

    ويدعم الشكل الحالي الذي يظهر في كووورة:
        09:4620 أغسطس 2026

    كما يتعامل مع المسافات غير التقليدية
    وأرقام العربية والفارسية.
    """

    if not text:
        return None

    text = (
        str(text)
        .translate(ARABIC_DIGITS)
    )

    # إزالة المسافات غير المرئية أو الخاصة.
    text = text.replace(
        "\u200f",
        " ",
    )
    text = text.replace(
        "\u200e",
        " ",
    )
    text = text.replace(
        "\u00a0",
        " ",
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
        # الشكل الحالي في كووورة:
        # HH:MMDD MONTH YYYY
        #
        # مثال:
        # 12:3129 أغسطس 2026
        rf"(\d{{1,2}}):(\d{{2}})\s*"
        rf"(\d{{1,2}})\s*"
        rf"{month_pattern}\s*"
        rf"(\d{{4}})",

        # الشكل:
        # HH:MM DD MONTH YYYY
        rf"(\d{{1,2}}):(\d{{2}})\s+"
        rf"(\d{{1,2}})\s+"
        rf"{month_pattern}\s+"
        rf"(\d{{4}})",

        # الشكل:
        # DD MONTH YYYY HH:MM
        rf"(\d{{1,2}})\s*"
        rf"{month_pattern}\s*"
        rf"(\d{{4}})\s*"
        rf"(\d{{1,2}}):(\d{{2}})",
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

            if len(groups) != 5:
                continue

            # التمييز بين:
            #
            # HH:MM DD MONTH YYYY
            #
            # و:
            #
            # DD MONTH YYYY HH:MM
            #
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

            month = ARABIC_MONTHS.get(
                month_name
            )

            if not month:
                continue

            if hour < 0 or hour > 23:
                continue

            if minute < 0 or minute > 59:
                continue

            naive_dt = datetime(
                year,
                month,
                day,
                hour,
                minute,
            )

            return (
                naive_dt
                .replace(
                    tzinfo=KOOORA_TIMEZONE
                )
                .astimezone(
                    timezone.utc
                )
            )

        except Exception:
            continue

    return None


def _extract_datetime_from_node_attributes(node):
    """
    البحث عن التاريخ في خصائص HTML للعقدة نفسها.

    يدعم:
    - datetime
    - content
    - data-datetime
    - data-time
    - data-date
    - title
    - aria-label
    """

    if node is None:
        return None

    attributes = [
        "datetime",
        "content",
        "data-datetime",
        "data-time",
        "data-date",
        "data-published",
        "data-published-at",
        "title",
        "aria-label",
    ]

    for attribute in attributes:
        value = node.get(
            attribute
        )

        if not value:
            continue

        parsed = _parse_datetime(
            value
        )

        if parsed:
            return parsed

        parsed = _parse_visible_datetime(
            value
        )

        if parsed:
            return parsed

    return None


def _extract_entry_time(anchor):
    """
    استخراج وقت نشر الخبر من البطاقة نفسها.

    الأولوية:
    1. خصائص الرابط نفسه.
    2. عناصر <time> داخل الرابط.
    3. النص الظاهر داخل الرابط.
    4. خصائص العناصر القريبة جدًا.
    5. النص الظاهر في الأب المباشر فقط.

    لا يتم الصعود لمسافات كبيرة داخل DOM
    لأن الأب قد يحتوي عدة أخبار مختلفة.
    """

    if anchor is None:
        return None

    # -------------------------------------------------
    # 1) خصائص الرابط نفسه.
    # -------------------------------------------------
    parsed = _extract_datetime_from_node_attributes(
        anchor
    )

    if parsed:
        return parsed

    # -------------------------------------------------
    # 2) عناصر <time> داخل الرابط نفسه.
    # -------------------------------------------------
    time_tags = anchor.find_all(
        "time"
    )

    for time_tag in time_tags:
        value = (
            time_tag.get("datetime")
            or time_tag.get("content")
            or time_tag.get_text(
                " ",
                strip=True,
            )
        )

        parsed = _parse_datetime(
            value
        )

        if parsed:
            return parsed

        parsed = _parse_visible_datetime(
            value
        )

        if parsed:
            return parsed

    # -------------------------------------------------
    # 3) النص الظاهر داخل الرابط نفسه.
    #
    # هذه أهم نقطة مع كووورة الحالية.
    # -------------------------------------------------
    anchor_text = anchor.get_text(
        " ",
        strip=True,
    )

    parsed = _parse_visible_datetime(
        anchor_text
    )

    if parsed:
        return parsed

    # -------------------------------------------------
    # 4) البحث في العناصر الأب القريبة جدًا،
    # ولكن في خصائصها فقط أولًا.
    # -------------------------------------------------
    parent = anchor.parent

    for _ in range(3):
        if parent is None:
            break

        parsed = _extract_datetime_from_node_attributes(
            parent
        )

        if parsed:
            return parsed

        # إذا كان الأب يحتوي على time واحد فقط،
        # يمكن اعتباره وقت البطاقة.
        time_tags = parent.find_all(
            "time"
        )

        if len(time_tags) == 1:
            time_tag = time_tags[0]

            value = (
                time_tag.get("datetime")
                or time_tag.get("content")
                or time_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            parsed = _parse_datetime(
                value
            )

            if parsed:
                return parsed

            parsed = _parse_visible_datetime(
                value
            )

            if parsed:
                return parsed

        parent = parent.parent

    # -------------------------------------------------
    # 5) محاولة أخيرة في الأب المباشر فقط.
    #
    # لا نصعد أكثر حتى لا نأخذ وقت خبر آخر.
    # -------------------------------------------------
    direct_parent = anchor.parent

    if direct_parent is not None:
        parent_text = direct_parent.get_text(
            " ",
            strip=True,
        )

        parsed = _parse_visible_datetime(
            parent_text
        )

        if parsed:
            return parsed

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

    total_anchors = 0
    article_candidates = 0
    parsed_times = 0
    missing_times = 0

    for anchor in soup.find_all(
        "a",
        href=True,
    ):
        total_anchors += 1

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

        article_candidates += 1

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

        if not published_dt:
            missing_times += 1

            # تشخيص محدود حتى لا يمتلئ GitHub Actions
            # بآلاف الأسطر.
            if missing_times <= 10:
                print(
                    "⚠️ تم اكتشاف رابط خبر لكن "
                    "تعذر استخراج وقت النشر:"
                )
                print(
                    f"   🔗 {link}"
                )
                print(
                    f"   📰 {title[:180]}"
                )

            continue

        parsed_times += 1

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

    print(
        "📊 تحليل صفحة كووورة: "
        f"anchors={total_anchors}, "
        f"article_candidates={article_candidates}, "
        f"parsed_times={parsed_times}, "
        f"missing_times={missing_times}"
    )

    return results


def _kooora_page_url(page: int) -> str:
    """
    إنشاء رابط صفحة كووورة الصحيح.

    الصفحة الأولى:
        /news

    الصفحات التالية في كووورة:
        /أخبار/2
        /أخبار/3
        ...
    """

    if page <= 1:
        return KOOORA_NEWS

    encoded_segment = urllib.parse.quote(
        KOOORA_PAGINATION_SEGMENT,
        safe="",
    )

    return (
        f"{KOOORA_BASE}/"
        f"{encoded_segment}/"
        f"{page}"
    )


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
        url = _kooora_page_url(
            page
        )

        try:
            print(
                f"🌐 فحص صفحة كووورة رقم {page}: "
                f"{url}"
            )

            raw_data = _fetch_url(
                url
            )

            print(
                f"📦 حجم الصفحة المستلمة: "
                f"{len(raw_data):,} bytes"
            )

            page_news = _parse_kooora_page(
                raw_data,
                "Kooora",
            )

            page_recent = 0
            page_old = 0
            page_processed = 0

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
                    page_old += 1
                    continue

                page_recent += 1

                if db.is_processed(
                    link,
                    title,
                ):
                    page_processed += 1
                    continue

                seen_links.add(link)
                seen_titles.add(
                    normalized_title
                )

                all_news.append(item)

            print(
                f"📊 صفحة {page}: "
                f"articles={len(page_news)}, "
                f"recent={page_recent}, "
                f"already_processed={page_processed}, "
                f"outside_window={page_old}"
            )

        except Exception as e:
            print(
                f"⚠️ تعذر جلب صفحة كووورة رقم "
                f"{page}: {e}"
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

    # الإبقاء على نافذة آخر 3 ساعات.
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
        6,
    )

    # ضمان أن عدد الصفحات رقم صحيح وموجب.
    try:
        pages = int(pages)
    except Exception:
        pages = 6

    pages = max(
        1,
        pages,
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
