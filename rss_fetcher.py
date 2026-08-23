"""
rss_fetcher.py
==============
جلب أخبار كووورة مباشرة.

لا يعتمد على Google News ولا على RSS خارجي.

يتم:
- جلب صفحة أخبار كووورة مباشرة.
- استخراج روابط المقالات الحالية.
- استخراج وقت النشر من بطاقة الخبر.
- مقارنة الوقت مع آخر 6 ساعات من بداية الدورة.
- منع روابط القوائم والمقالات التحليلية.
- محاولة استخدام الصفحة التالية عند الحاجة.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag


# =========================================================
# Kooora
# =========================================================

BASE_URL = "https://www.kooora.com"
NEWS_URL = f"{BASE_URL}/أخبار"

LOOKBACK_HOURS = 6

SOURCE_TZ = timezone(
    timedelta(hours=3)
)

REQUEST_TIMEOUT = 30

MAX_PAGES = 10


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)


# =========================================================
# Arabic dates
# =========================================================

ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩",
    "0123456789",
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


MONTH_PATTERN = "|".join(
    re.escape(month)
    for month in ARABIC_MONTHS
)


# =========================================================
# Session
# =========================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept-Language": (
            "ar-SA,ar;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Referer": BASE_URL + "/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
)


# =========================================================
# Text
# =========================================================

def _clean_text(
    text: str,
) -> str:
    text = str(
        text or ""
    )

    text = text.replace(
        "\u00a0",
        " ",
    )

    text = text.replace(
        "\r",
        " ",
    )

    text = text.replace(
        "\n",
        " ",
    )

    text = text.replace(
        "\t",
        " ",
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _normalize_digits(
    text: str,
) -> str:
    return (
        text or ""
    ).translate(
        ARABIC_DIGITS
    )


# =========================================================
# URL
# =========================================================

def _canonical_url(
    url: str,
) -> str:

    url = urljoin(
        BASE_URL + "/",
        url,
    )

    parsed = urlparse(
        url
    )

    host = (
        parsed.netloc
        or ""
    ).lower()

    if host in {
        "kooora.com",
        "www.kooora.com",
    }:
        host = "www.kooora.com"

    path = unquote(
        parsed.path
        or "/"
    )

    path = re.sub(
        r"/{2,}",
        "/",
        path,
    )

    path = (
        path.rstrip("/")
        or "/"
    )

    return parsed._replace(
        netloc=host,
        path=path,
        query="",
        fragment="",
    ).geturl()


# =========================================================
# Article URL
# =========================================================

def _is_article_url(
    url: str,
) -> bool:

    parsed = urlparse(
        url
    )

    host = (
        parsed.netloc
        or ""
    ).lower()

    if host not in {
        "",
        "kooora.com",
        "www.kooora.com",
    }:
        return False

    path = unquote(
        parsed.path
        or "/"
    ).lower()

    if path in {
        "/",
        "/أخبار",
        "/news",
    }:
        return False

    # Current Kooora article identifiers.
    if not re.search(
        r"/blt[a-z0-9_-]+/?$",
        path,
        flags=re.IGNORECASE,
    ):
        return False

    # Do not collect list/article-analysis pages.
    if any(
        section in path
        for section in (
            "/القوائم/",
            "/القوائم",
            "/lists/",
            "/list/",
        )
    ):
        return False

    return (
        "/أخبار/" in path
        or "/news/" in path
    )


# =========================================================
# Date parser
# =========================================================

def _build_source_datetime(
    day: int,
    month: int,
    year: int,
    hour: int,
    minute: int,
) -> datetime | None:

    try:
        return datetime(
            year,
            month,
            day,
            hour,
            minute,
            tzinfo=SOURCE_TZ,
        )

    except ValueError:
        return None


def _parse_datetime(
    text: str,
) -> datetime | None:

    raw = _normalize_digits(
        text
    )

    raw = _clean_text(
        raw
    )

    if not raw:
        return None

    # -----------------------------------------------------
    # ISO timestamps
    # -----------------------------------------------------

    try:

        candidate = raw.replace(
            "Z",
            "+00:00",
        )

        if re.search(
            r"\d{4}-\d{2}-\d{2}",
            candidate,
        ):

            dt = datetime.fromisoformat(
                candidate
            )

            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=SOURCE_TZ
                )

            return dt.astimezone(
                SOURCE_TZ
            )

    except ValueError:
        pass

    # -----------------------------------------------------
    # Arabic date + time
    #
    # Example:
    # 03:40 23 أغسطس 2026
    # -----------------------------------------------------

    patterns = [
        rf"(\d{{1,2}})\s*[:٫.]\s*(\d{{2}})\s*(\d{{1,2}})\s+({MONTH_PATTERN})\s+(20\d{{2}})(?!\d)",

        rf"(\d{{1,2}})\s+({MONTH_PATTERN})\s+(20\d{{2}})\s*(\d{{1,2}})\s*[:٫.]\s*(\d{{2}})(?!\d)",
    ]

    matches = []

    for pattern_index, pattern in enumerate(
        patterns
    ):

        for match in re.finditer(
            pattern,
            raw,
            flags=re.IGNORECASE,
        ):

            matches.append(
                (
                    pattern_index,
                    match,
                )
            )

    if matches:

        pattern_index, match = max(
            matches,
            key=lambda item: item[1].start(),
        )

        if pattern_index == 0:

            hour = int(
                match.group(1)
            )

            minute = int(
                match.group(2)
            )

            day = int(
                match.group(3)
            )

            month_name = match.group(4)

            year = int(
                match.group(5)
            )

        else:

            day = int(
                match.group(1)
            )

            month_name = match.group(2)

            year = int(
                match.group(3)
            )

            hour = int(
                match.group(4)
            )

            minute = int(
                match.group(5)
            )

        month = ARABIC_MONTHS.get(
            month_name
        )

        if month is None:

            month = ARABIC_MONTHS.get(
                month_name.replace(
                    "ا",
                    "أ",
                )
            )

        if month is not None:

            return _build_source_datetime(
                day=day,
                month=month,
                year=year,
                hour=hour,
                minute=minute,
            )

    # -----------------------------------------------------
    # Numeric dates
    # -----------------------------------------------------

    numeric = re.search(
        r"(\d{1,2})\s*[:٫.]\s*(\d{2})\s+"
        r"(\d{1,2})[/-](\d{1,2})[/-](20\d{2})",
        raw,
    )

    if numeric:

        return _build_source_datetime(
            day=int(
                numeric.group(3)
            ),
            month=int(
                numeric.group(4)
            ),
            year=int(
                numeric.group(5)
            ),
            hour=int(
                numeric.group(1)
            ),
            minute=int(
                numeric.group(2)
            ),
        )

    numeric_reverse = re.search(
        r"(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\s+"
        r"(\d{1,2})\s*[:٫.]\s*(\d{2})",
        raw,
    )

    if numeric_reverse:

        return _build_source_datetime(
            day=int(
                numeric_reverse.group(1)
            ),
            month=int(
                numeric_reverse.group(2)
            ),
            year=int(
                numeric_reverse.group(3)
            ),
            hour=int(
                numeric_reverse.group(4)
            ),
            minute=int(
                numeric_reverse.group(5)
            ),
        )

    return None


# =========================================================
# Metadata date
# =========================================================

def _first_meta(
    soup: BeautifulSoup,
    names: Iterable[str],
) -> str | None:

    for name in names:

        tag = (
            soup.find(
                "meta",
                attrs={
                    "property": name
                },
            )
            or soup.find(
                "meta",
                attrs={
                    "name": name
                },
            )
        )

        if (
            tag
            and tag.get(
                "content"
            )
        ):

            return tag.get(
                "content"
            ).strip()

    return None


def _extract_json_ld_dates(
    soup: BeautifulSoup,
) -> list[str]:

    values = []

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):

        raw = (
            script.string
            or script.get_text()
        )

        if not raw:
            continue

        try:
            import json

            data = json.loads(
                raw
            )

        except Exception:
            continue

        blocks = []

        if isinstance(
            data,
            dict,
        ):

            blocks.append(
                data
            )

            graph = data.get(
                "@graph"
            )

            if isinstance(
                graph,
                list,
            ):

                blocks.extend(
                    item
                    for item in graph
                    if isinstance(
                        item,
                        dict,
                    )
                )

        elif isinstance(
            data,
            list,
        ):

            blocks.extend(
                item
                for item in data
                if isinstance(
                    item,
                    dict,
                )
            )

        for block in blocks:

            for key in (
                "datePublished",
                "dateCreated",
                "dateModified",
            ):

                value = block.get(
                    key
                )

                if isinstance(
                    value,
                    str,
                ):

                    values.append(
                        value
                    )

    return values


# =========================================================
# HTTP
# =========================================================

def _get(
    url: str,
) -> requests.Response:

    response = SESSION.get(
        url,
        timeout=REQUEST_TIMEOUT,
        allow_redirects=True,
    )

    response.raise_for_status()

    if not response.encoding:
        response.encoding = (
            response.apparent_encoding
            or "utf-8"
        )

    return response


# =========================================================
# Candidate date
# =========================================================

def _find_card_date(
    node: Tag,
) -> datetime | None:

    # First: exact anchor text.
    exact_text = _clean_text(
        node.get_text(
            " ",
            strip=True,
        )
    )

    dt = _parse_datetime(
        exact_text
    )

    if dt:
        return dt

    # Attributes.
    for attr in (
        "datetime",
        "data-datetime",
        "data-published",
        "data-publish-date",
        "data-date",
        "data-time",
        "title",
        "aria-label",
    ):

        value = node.get(
            attr
        )

        if not value:
            continue

        dt = _parse_datetime(
            value
        )

        if dt:
            return dt

    # Small parent only.
    current = node

    for _ in range(3):

        parent = (
            current.parent
            if isinstance(
                current.parent,
                Tag,
            )
            else None
        )

        if parent is None:
            break

        parent_text = _clean_text(
            parent.get_text(
                " ",
                strip=True,
            )
        )

        if len(
            parent_text
        ) <= 1200:

            dt = _parse_datetime(
                parent_text
            )

            if dt:
                return dt

        for time_tag in parent.find_all(
            "time"
        )[:3]:

            value = (
                time_tag.get(
                    "datetime"
                )
                or time_tag.get(
                    "data-datetime"
                )
                or time_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            dt = _parse_datetime(
                value
            )

            if dt:
                return dt

        current = parent

    return None


# =========================================================
# Article fallback date
# =========================================================

def _article_date(
    url: str,
) -> datetime | None:

    try:

        response = _get(
            url
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        for name in (
            "article:published_time",
            "datePublished",
            "publication_date",
            "date",
        ):

            value = _first_meta(
                soup,
                (name,),
            )

            if value:

                dt = _parse_datetime(
                    value
                )

                if dt:
                    return dt

        for value in _extract_json_ld_dates(
            soup
        ):

            dt = _parse_datetime(
                value
            )

            if dt:
                return dt

        for time_tag in soup.find_all(
            "time"
        ):

            value = (
                time_tag.get(
                    "datetime"
                )
                or time_tag.get_text(
                    " ",
                    strip=True,
                )
            )

            dt = _parse_datetime(
                value
            )

            if dt:
                return dt

    except Exception as exc:

        print(
            f"⚠️ فشل جلب تاريخ المقال: "
            f"{url} -> {exc}"
        )

    return None


# =========================================================
# Title
# =========================================================

def _extract_title(
    node: Tag,
) -> str:

    # Prefer accessible title attributes.
    for attr in (
        "title",
        "aria-label",
    ):

        value = node.get(
            attr
        )

        if value:

            value = _clean_text(
                value
            )

            if len(value) >= 10:

                return value

    text = _clean_text(
        node.get_text(
            " ",
            strip=True,
        )
    )

    # Remove timestamp from end.
    text = re.sub(
        rf"\d{{1,2}}\s*[:٫.]\s*\d{{2}}\s+\d{{1,2}}\s+({MONTH_PATTERN})\s+20\d{{2}}",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    return text


# =========================================================
# Pagination
# =========================================================

def _find_next_page(
    soup: BeautifulSoup,
    current_url: str,
) -> str | None:

    for a in soup.find_all(
        "a",
        href=True,
    ):

        text = _clean_text(
            a.get_text(
                " ",
                strip=True,
            )
        ).lower()

        if (
            text in {
                "أقدم",
                "الأقدم",
                "التالي",
                "الصفحة التالية",
                "next",
                "older",
            }
            or "أقدم" in text
        ):

            target = urljoin(
                current_url,
                a.get(
                    "href"
                ),
            )

            target = _canonical_url(
                target
            )

            if target != _canonical_url(
                current_url
            ):

                return target

    return None


# =========================================================
# Main function
# =========================================================

def fetch_prioritized_news(
    cycle_start: datetime,
) -> list[dict]:

    print(
        "🔎 محاولة جلب الأخبار مباشرة من كووورة..."
    )

    print(
        "⏱️ نافذة الفحص: آخر 6 ساعات من وقت بدء الدورة."
    )

    if cycle_start.tzinfo is None:

        cycle_start = cycle_start.replace(
            tzinfo=timezone.utc
        )

    # Convert cycle time to Kooora/Saudi timezone.
    cycle_start_source = cycle_start.astimezone(
        SOURCE_TZ
    )

    cutoff_source = (
        cycle_start_source
        - timedelta(
            hours=LOOKBACK_HOURS
        )
    )

    print(
        f"🕐 بداية الدورة UTC: "
        f"{cycle_start.isoformat()}"
    )

    print(
        f"🕐 بداية الدورة بتوقيت كووورة: "
        f"{cycle_start_source.isoformat()}"
    )

    print(
        f"🕐 بداية النافذة: "
        f"{cutoff_source.isoformat()}"
    )

    results = []

    seen_urls = set()

    current_url = NEWS_URL

    for page_number in range(
        1,
        MAX_PAGES + 1,
    ):

        try:

            response = _get(
                current_url
            )

        except Exception as exc:

            print(
                f"❌ فشل جلب صفحة كووورة: "
                f"{current_url} -> {exc}"
            )

            break

        effective_url = (
            response.url
            or current_url
        )

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )

        page_candidates = []

        article_links = 0

        dated_links = 0

        in_window = 0

        for a in soup.find_all(
            "a",
            href=True,
        ):

            href = _canonical_url(
                urljoin(
                    effective_url,
                    a.get(
                        "href",
                        "",
                    ),
                )
            )

            if (
                href in seen_urls
                or not _is_article_url(
                    href
                )
            ):
                continue

            title = _extract_title(
                a
            )

            if (
                not title
                or len(title) < 8
            ):
                continue

            article_links += 1

            published = _find_card_date(
                a
            )

            # Fallback to article itself.
            if not published:

                published = _article_date(
                    href
                )

            if not published:

                print(
                    f"⚠️ تعذر استخراج تاريخ: "
                    f"{href}"
                )

                continue

            dated_links += 1

            seen_urls.add(
                href
            )

            page_candidates.append(
                (
                    published,
                    title,
                    href,
                )
            )

            # --------------------------------------------------
            # The timestamp is already in SOURCE_TZ.
            # No UTC conversion is needed here.
            # --------------------------------------------------

            if (
                cutoff_source
                <= published
                <= cycle_start_source
                + timedelta(
                    minutes=15
                )
            ):

                in_window += 1

                results.append(
                    {
                        "title": title,
                        "link": href,
                        "url": href,
                        "matched_keyword": "",
                        "published_at": published,
                    }
                )

                print(
                    "✅ خبر ضمن النافذة: "
                    f"{published.isoformat()} | "
                    f"{title[:100]} | "
                    f"{href}"
                )

        print(
            f"📄 صفحة {page_number}: "
            f"article_links={article_links} "
            f"dated_links={dated_links} "
            f"in_window={in_window}"
        )

        # -----------------------------------------------------
        # If this page has timestamps and its newest article
        # is already older than the six-hour cutoff, older
        # pages cannot contain newer news.
        # -----------------------------------------------------

        if page_candidates:

            newest = max(
                item[0]
                for item in page_candidates
            )

            oldest = min(
                item[0]
                for item in page_candidates
            )

            print(
                f"🕐 نطاق الصفحة: "
                f"{oldest.isoformat()} -> "
                f"{newest.isoformat()}"
            )

            if newest < cutoff_source:

                print(
                    "🛑 أحدث خبر في الصفحة "
                    "أقدم من نافذة الست ساعات."
                )

                break

        next_page = _find_next_page(
            soup,
            effective_url,
        )

        if not next_page:

            print(
                "ℹ️ لا توجد صفحة أقدم أخرى."
            )

            break

        if (
            next_page in seen_urls
        ):

            break

        current_url = next_page

    # ---------------------------------------------------------
    # Remove duplicates.
    # ---------------------------------------------------------

    unique = {}

    for item in results:

        link = item[
            "link"
        ]

        if link not in unique:

            unique[
                link
            ] = item

    results = list(
        unique.values()
    )

    results.sort(
        key=lambda item: item[
            "published_at"
        ],
        reverse=True,
    )

    print(
        f"📰 إجمالي أخبار كووورة "
        f"داخل آخر {LOOKBACK_HOURS} ساعات: "
        f"{len(results)}"
    )

    return results
