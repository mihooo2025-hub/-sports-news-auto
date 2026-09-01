"""
rss_fetcher.py
==============
يجلب أخبار كووورة مباشرة، مع Google News RSS كخطة احتياطية.

الأخبار الجديدة:
- تعتمد نافذة آخر 3 ساعات من وقت النشر الحقيقي.

الأخبار التي فشلت سابقًا:
- يتم استعادتها من قاعدة البيانات.
- تتم إعادة محاولة التحقق منها لمدة 6 ساعات.
- لا تعتمد إعادة المحاولة على بقاء الخبر ظاهرًا في
  صفحات كووورة الحالية.

آلية التحقق:
1. اكتشاف روابط المقالات من صفحات كووورة.
2. استبعاد صفحات المباريات والفرق واللاعبين والفيديو والبث وغيرها.
3. استبعاد الأخبار التي يحتوي عنوانها الأصلي على:
   - القنوات الناقلة
   - القناة الناقلة
4. استخدام وقت القائمة كفلتر أولي واسع فقط.
5. فتح صفحة الخبر نفسها للمرشحين.
6. استخراج وقت النشر الحقيقي من صفحة المقال.
7. اعتماد نافذة آخر 3 ساعات للأخبار الجديدة.
8. الأخبار التي تفشل أثناء فتح صفحة الخبر أو التحقق منها
   تحفظ في قاعدة البيانات لإعادة المحاولة لمدة 6 ساعات.
"""

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

import feedparser
from bs4 import BeautifulSoup

from config import CONFIG
import db


# ============================================================
# إعدادات كووورة
# ============================================================

KOOORA_BASE = "https://www.kooora.com"
KOOORA_NEWS = f"{KOOORA_BASE}/news"

KOOORA_PAGINATION_SEGMENT = "أخبار"

KOOORA_TIMEZONE = timezone(
    timedelta(hours=3)
)

FINAL_MAX_AGE_HOURS = 3
PRECHECK_MAX_AGE_HOURS = 9
FAILED_RETRY_HOURS = 6


# ============================================================
# فلترة عناوين الأخبار
# ============================================================

TITLE_FILTER_PHRASES = (
    "القنوات الناقلة",
    "القناة الناقلة",
)

_FILTERED_TITLE_ITEMS = []
_FILTERED_TITLE_KEYS = set()


def _reset_title_filter_report():
    global _FILTERED_TITLE_ITEMS
    global _FILTERED_TITLE_KEYS

    _FILTERED_TITLE_ITEMS = []
    _FILTERED_TITLE_KEYS = set()


def _normalize_filter_title(title: str) -> str:
    if not title:
        return ""

    title = html.unescape(
        str(title)
    )

    return re.sub(
        r"\s+",
        " ",
        title,
    ).strip()


def _is_filtered_title(title: str) -> bool:
    normalized_title = _normalize_filter_title(
        title
    )

    return any(
        phrase in normalized_title
        for phrase in TITLE_FILTER_PHRASES
    )


def _record_filtered_title(
    title: str,
    link: str = "",
    source: str = "Kooora",
):
    global _FILTERED_TITLE_ITEMS
    global _FILTERED_TITLE_KEYS

    title = _normalize_filter_title(
        title
    )

    link = str(
        link or ""
    ).strip()

    key = link or title.lower()

    if not key:
        return

    if key in _FILTERED_TITLE_KEYS:
        return

    _FILTERED_TITLE_KEYS.add(
        key
    )

    _FILTERED_TITLE_ITEMS.append(
        {
            "title": title,
            "link": link,
            "source": source,
        }
    )

    print(
        "\n🚫 استبعاد خبر بسبب عنوانه:"
    )

    print(
        f"   📰 {title[:180]}"
    )

    if link:
        print(
            f"   🔗 {link}"
        )


def get_filtered_title_items() -> list:
    return list(
        _FILTERED_TITLE_ITEMS
    )


# ============================================================
# الأشهر والأرقام العربية
# ============================================================

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


# ============================================================
# نطاقات ليست مقالات
# ============================================================

NON_ARTICLE_SEGMENTS = {
    "match",
    "matches",
    "game",
    "games",
    "مباراة",
    "مباريات",

    "live",
    "livescore",
    "livescores",
    "live-scores",
    "بث",
    "مباشر",

    "video",
    "videos",
    "فيديو",
    "فيديوهات",

    "photo",
    "photos",
    "gallery",
    "صور",
    "معرض",

    "team",
    "teams",
    "فريق",
    "فرق",

    "player",
    "players",
    "لاعب",
    "لاعبين",

    "competition",
    "competitions",
    "tournament",
    "tournaments",
    "league",
    "leagues",
    "بطولة",
    "بطولات",
    "دوري",

    "category",
    "categories",
    "tag",
    "tags",
    "standings",
    "fixtures",
    "results",
    "ترتيب",
    "نتائج",
    "مواعيد",

    "search",
    "author",
    "authors",
    "بحث",
    "كاتب",
    "كتاب",
}


# ============================================================
# أدوات عامة
# ============================================================

def _fetch_url(
    url: str,
    timeout: int = 20,
):
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
        return response.read(), response.geturl()


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


def _normalize_title(title: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        title.strip(),
    ).lower()


# ============================================================
# التواريخ
# ============================================================

def _parse_datetime(value: str):
    if not value:
        return None

    value = str(value).strip()

    if not value:
        return None

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
    if not text:
        return None

    text = str(text).translate(
        ARABIC_DIGITS
    )

    for old, new in [
        ("\u200f", " "),
        ("\u200e", " "),
        ("\u00a0", " "),
    ]:
        text = text.replace(
            old,
            new,
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
        rf"(\d{{1,2}}):(\d{{2}})\s*"
        rf"(\d{{1,2}})\s*"
        rf"{month_pattern}\s*"
        rf"(\d{{4}})",

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

            first = match.group(0)

            if ":" in first.split()[0]:
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

            if not 0 <= hour <= 23:
                continue

            if not 0 <= minute <= 59:
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


def _is_recent(
    published_dt,
    cycle_start: datetime,
    max_age_hours: int,
) -> bool:

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
            - timedelta(
                hours=max_age_hours
            )
        )

        return (
            cutoff
            <= published_dt
            <= cycle_start
        )

    except Exception:
        return False


# ============================================================
# استخراج التاريخ من خصائص HTML
# ============================================================

def _extract_datetime_from_node_attributes(node):

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

        value = node.get(attribute)

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

    if anchor is None:
        return None

    parsed = _extract_datetime_from_node_attributes(
        anchor
    )

    if parsed:
        return parsed

    for time_tag in anchor.find_all("time"):

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

    anchor_text = anchor.get_text(
        " ",
        strip=True,
    )

    parsed = _parse_visible_datetime(
        anchor_text
    )

    if parsed:
        return parsed

    parent = anchor.parent

    for _ in range(3):

        if parent is None:
            break

        parsed = _extract_datetime_from_node_attributes(
            parent
        )

        if parsed:
            return parsed

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


# ============================================================
# التحقق من نوع الرابط
# ============================================================

def _article_url(url: str) -> bool:

    try:

        parsed = urllib.parse.urlparse(url)

        # استخدام hostname بدل netloc حتى لا يؤدي وجود
        # :443 أو أي منفذ في الرابط إلى رفض رابط كووورة.
        hostname = (
            parsed.hostname or ""
        ).lower()

        if hostname not in {
            "kooora.com",
            "www.kooora.com",
        }:
            return False

        path = urllib.parse.unquote(
            parsed.path
        ).rstrip("/")

        parts = [
            p
            for p in path.split("/")
            if p
        ]

        if len(parts) < 2:
            return False

        if path.lower() in {
            "/news",
            "/أخبار",
        }:
            return False

        if re.fullmatch(
            r"news/\d+",
            path.lstrip("/"),
            flags=re.IGNORECASE,
        ):
            return False

        if re.fullmatch(
            r"أخبار/\d+",
            path.lstrip("/"),
        ):
            return False

        if any(
            part.lower()
            in NON_ARTICLE_SEGMENTS
            for part in parts
        ):
            return False

        blocked_exact_segments = {
            "كرة القدم/مباراة",
            "كرة القدم/مباريات",
            "كرة القدم/فيديو",
            "كرة القدم/فيديوهات",
            "كرة القدم/بث",
            "كرة القدم/مباشر",
            "كرة القدم/نتائج",
            "كرة القدم/ترتيب",
            "كرة القدم/مواعيد",
        }

        normalized_path = "/".join(
            parts[:2]
        ).lower()

        if normalized_path in {
            value.lower()
            for value in blocked_exact_segments
        }:
            return False

        last = parts[-1]

        if re.fullmatch(
            r"\d+",
            last,
        ):
            return True

        if last.lower().startswith("blt"):
            return True

        if re.search(
            r"(?:^|-)\d{4,}(?:-|$)",
            last,
        ):
            return True

        if any(
            re.fullmatch(
                r"\d{4,}",
                part,
            )
            for part in parts[:-1]
        ):
            return True

        return True

    except Exception:
        return False


# ============================================================
# استخراج وقت النشر الحقيقي
# ============================================================

def _parse_jsonld_datetime(
    soup: BeautifulSoup,
):

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):

        raw = script.string or script.get_text(
            strip=True
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = []

        if isinstance(data, dict):

            objects.append(data)

            graph = data.get("@graph")

            if isinstance(graph, list):
                objects.extend(graph)

        elif isinstance(data, list):
            objects.extend(data)

        for obj in objects:

            if not isinstance(obj, dict):
                continue

            obj_type = obj.get("@type", "")

            if isinstance(
                obj_type,
                list,
            ):
                obj_type = " ".join(
                    str(x)
                    for x in obj_type
                )

            obj_type = str(
                obj_type
            ).lower()

            if (
                "article" not in obj_type
                and "news" not in obj_type
            ):
                continue

            for key in [
                "datePublished",
                "dateCreated",
                "dateModified",
            ]:

                value = obj.get(key)

                parsed = _parse_datetime(
                    value
                )

                if parsed:
                    return parsed

    return None


def _extract_article_datetime_from_html(
    soup: BeautifulSoup,
):

    parsed = _parse_jsonld_datetime(
        soup
    )

    if parsed:
        return parsed, "jsonld"

    meta_names = [
        ("property", "article:published_time"),
        ("property", "og:published_time"),
        ("name", "article:published_time"),
        ("name", "datePublished"),
        ("name", "publishdate"),
        ("name", "published"),
        ("name", "publication_date"),
    ]

    for attr, name in meta_names:

        tag = soup.find(
            "meta",
            attrs={
                attr: name
            },
        )

        if not tag:
            continue

        value = tag.get("content")

        parsed = _parse_datetime(
            value
        )

        if parsed:
            return parsed, f"meta:{name}"

        parsed = _parse_visible_datetime(
            value
        )

        if parsed:
            return parsed, f"meta:{name}"

    for time_tag in soup.find_all("time"):

        parsed = _extract_datetime_from_node_attributes(
            time_tag
        )

        if parsed:
            return parsed, "time_attribute"

        text = time_tag.get_text(
            " ",
            strip=True,
        )

        parsed = _parse_visible_datetime(
            text
        )

        if parsed:
            return parsed, "time_text"

    date_keywords = [
        "published",
        "publish",
        "datepublished",
        "publication",
        "تاريخ النشر",
        "تاريخ-النشر",
        "وقت النشر",
        "وقت-النشر",
        "نشر",
    ]

    for tag in soup.find_all(True):

        classes = " ".join(
            tag.get("class", [])
        ).lower()

        tag_id = str(
            tag.get("id", "")
        ).lower()

        marker = f"{classes} {tag_id}"

        if not any(
            keyword in marker
            for keyword in date_keywords
        ):
            continue

        parsed = _extract_datetime_from_node_attributes(
            tag
        )

        if parsed:
            return parsed, "published_element_attribute"

        text = tag.get_text(
            " ",
            strip=True,
        )

        if len(text) > 150:
            continue

        parsed = _parse_visible_datetime(
            text
        )

        if parsed:
            return parsed, "published_element_text"

    return None, None


# ============================================================
# التحقق من أن الصفحة مقال إخباري
# ============================================================

def _is_actual_news_article(
    soup: BeautifulSoup,
) -> bool:

    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):

        raw = script.string or script.get_text(
            strip=True
        )

        if not raw:
            continue

        try:
            data = json.loads(raw)
        except Exception:
            continue

        objects = []

        if isinstance(data, dict):

            objects.append(data)

            graph = data.get("@graph")

            if isinstance(graph, list):
                objects.extend(graph)

        elif isinstance(data, list):
            objects.extend(data)

        for obj in objects:

            if not isinstance(obj, dict):
                continue

            obj_type = obj.get("@type", "")

            if isinstance(
                obj_type,
                list,
            ):

                types = [
                    str(x).lower()
                    for x in obj_type
                ]

            else:

                types = [
                    str(obj_type).lower()
                ]

            if any(
                x in {
                    "newsarticle",
                    "article",
                    "reportage",
                }
                for x in types
            ):
                return True

    if soup.find("article"):
        return True

    headline = soup.find(
        attrs={
            "itemprop": "headline"
        }
    )

    if headline:
        return True

    for meta_name in [
        "og:type",
        "twitter:card",
    ]:

        tag = soup.find(
            "meta",
            attrs={
                "property": meta_name
            },
        ) or soup.find(
            "meta",
            attrs={
                "name": meta_name
            },
        )

        if tag:

            value = str(
                tag.get("content", "")
            ).lower()

            if value in {
                "article",
                "news",
            }:
                return True

    return False


# ============================================================
# التحقق الكامل من صفحة الخبر
# ============================================================

def _verify_article_page(
    url: str,
    cycle_start: datetime,
    max_age: int,
):

    try:

        raw_data, resolved_url = _fetch_url(
            url
        )

        soup = BeautifulSoup(
            raw_data,
            "html.parser",
        )

    except Exception as e:

        print(
            f"   ⚠️ تعذر فتح صفحة الخبر للتحقق: {e}"
        )

        return {
            "success": False,
            "retryable": True,
            "reason": "fetch_failed",
        }

    if not _article_url(
        resolved_url
    ):

        print(
            "   🚫 الرابط النهائي ليس رابط مقال:"
        )

        print(
            f"      {resolved_url}"
        )

        return {
            "success": False,
            "retryable": False,
            "reason": "not_article_url",
        }

    if not _is_actual_news_article(
        soup
    ):

        print(
            "   🚫 الصفحة ليست مقالًا إخباريًا."
        )

        return {
            "success": False,
            "retryable": False,
            "reason": "not_news_article",
        }

    published_dt, method = (
        _extract_article_datetime_from_html(
            soup
        )
    )

    if not published_dt:

        print(
            "   ⚠️ الصفحة تبدو مقالًا، "
            "لكن تعذر تحديد وقت النشر الحقيقي."
        )

        return {
            "success": False,
            "retryable": True,
            "reason": "missing_publish_time",
        }

    if not _is_recent(
        published_dt,
        cycle_start,
        max_age,
    ):

        print(
            "   ⏭️ المقال قديم خارج نافذة الـ3 ساعات."
        )

        print(
            f"      وقت النشر الحقيقي: "
            f"{published_dt.isoformat()}"
        )

        return {
            "success": False,
            "retryable": False,
            "reason": "outside_final_window",
        }

    return {
        "success": True,
        "retryable": False,
        "published_dt": published_dt,
        "resolved_url": resolved_url,
        "date_method": method,
    }


# ============================================================
# تحليل صفحات قائمة كووورة
# ============================================================

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
    rejected_non_articles = 0
    filtered_titles = 0

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

        link = link.split(
            "#",
            1,
        )[0]

        if not _article_url(link):

            rejected_non_articles += 1
            continue

        article_candidates += 1

        if link in seen:
            continue

        seen.add(link)

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

        if _is_filtered_title(
            title
        ):

            filtered_titles += 1

            _record_filtered_title(
                title=title,
                link=link,
                source=source_name,
            )

            continue

        published_dt = _extract_entry_time(
            anchor
        )

        if not published_dt:

            missing_times += 1

            if missing_times <= 10:

                print(
                    "⚠️ تم اكتشاف رابط محتمل لمقال "
                    "لكن تعذر استخراج وقت القائمة:"
                )

                print(
                    f"   🔗 {link}"
                )

                print(
                    f"   📰 {title[:180]}"
                )

            results.append(
                {
                    "title": title,
                    "link": link,
                    "list_published_dt": None,
                    "source": source_name,
                    "matched_keyword": source_name,
                }
            )

            continue

        parsed_times += 1

        results.append(
            {
                "title": title,
                "link": link,
                "list_published_dt": published_dt,
                "source": source_name,
                "matched_keyword": source_name,
            }
        )

    print(
        "📊 تحليل صفحة كووورة: "
        f"anchors={total_anchors}, "
        f"article_candidates={article_candidates}, "
        f"parsed_times={parsed_times}, "
        f"missing_times={missing_times}, "
        f"rejected_non_articles={rejected_non_articles}, "
        f"filtered_titles={filtered_titles}"
    )

    return results


# ============================================================
# روابط صفحات كووورة
# ============================================================

def _kooora_page_url(
    page: int,
) -> str:

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


# ============================================================
# تسجيل فشل قابل لإعادة المحاولة
# ============================================================

def _save_retryable_failure(
    link: str,
    title: str,
):

    try:

        db.mark_processed(
            url=link,
            title=title,
            status="publish_failed",
        )

        print(
            "💾 تم حفظ الخبر كفشل قابل لإعادة المحاولة "
            "لمدة 6 ساعات."
        )

    except Exception as e:

        print(
            f"⚠️ تعذر حفظ الخبر الفاشل في قاعدة البيانات: {e}"
        )


# ============================================================
# الجلب المباشر من كووورة
# ============================================================

def _fetch_kooora_direct(
    cycle_start: datetime,
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

            raw_data, _ = _fetch_url(
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
            page_verified = 0
            page_verification_failed = 0
            page_retry_saved = 0

            for item in page_news:

                link = item["link"]
                title = item["title"]

                if link in seen_links:
                    continue

                normalized_title = (
                    _normalize_title(title)
                )

                if normalized_title in seen_titles:
                    continue

                list_dt = item.get(
                    "list_published_dt"
                )

                if (
                    list_dt
                    and not _is_recent(
                        list_dt,
                        cycle_start,
                        PRECHECK_MAX_AGE_HOURS,
                    )
                ):

                    page_old += 1
                    continue

                if db.is_processed(
                    link,
                    title,
                ):

                    page_processed += 1
                    continue

                print(
                    "\n🔎 التحقق من صفحة الخبر:"
                )

                print(
                    f"   📰 {title[:180]}"
                )

                print(
                    f"   🔗 {link}"
                )

                verified = _verify_article_page(
                    link,
                    cycle_start,
                    FINAL_MAX_AGE_HOURS,
                )

                if not verified.get(
                    "success"
                ):

                    page_verification_failed += 1

                    if verified.get(
                        "retryable"
                    ):

                        _save_retryable_failure(
                            link,
                            title,
                        )

                        page_retry_saved += 1

                    continue

                real_published_dt = verified[
                    "published_dt"
                ]

                resolved_url = verified[
                    "resolved_url"
                ]

                page_verified += 1

                print(
                    "   ✅ تم التأكد من أن الصفحة "
                    "مقال إخباري حديث."
                )

                print(
                    f"   🕐 وقت النشر الحقيقي: "
                    f"{real_published_dt.isoformat()}"
                )

                print(
                    f"   🔍 مصدر الوقت: "
                    f"{verified['date_method']}"
                )

                if not _is_recent(
                    real_published_dt,
                    cycle_start,
                    FINAL_MAX_AGE_HOURS,
                ):
                    continue

                seen_links.add(
                    link
                )

                seen_titles.add(
                    normalized_title
                )

                all_news.append(
                    {
                        "title": title,
                        "link": link,
                        "published": real_published_dt.isoformat(),
                        "published_dt": real_published_dt,
                        "source": "Kooora",
                        "matched_keyword": "Kooora",
                        "resolved_url": resolved_url,
                    }
                )

                page_recent += 1

            print(
                f"📊 صفحة {page}: "
                f"articles={len(page_news)}, "
                f"verified_recent={page_recent}, "
                f"verified={page_verified}, "
                f"already_processed={page_processed}, "
                f"outside_precheck={page_old}, "
                f"verification_failed={page_verification_failed}, "
                f"retry_saved={page_retry_saved}"
            )

        except Exception as e:

            print(
                f"⚠️ تعذر جلب صفحة كووورة رقم "
                f"{page}: {e}"
            )

    return all_news


# ============================================================
# إعادة محاولة الأخبار الفاشلة
# ============================================================

def _retry_failed_news(
    cycle_start: datetime,
) -> list:

    failed_items = db.get_retryable_failed_news(
        limit=200
    )

    if not failed_items:

        print(
            "ℹ️ لا توجد أخبار فاشلة قابلة لإعادة المحاولة."
        )

        return []

    print(
        f"\n🔄 توجد {len(failed_items)} "
        "أخبار فاشلة ضمن نافذة إعادة المحاولة."
    )

    retry_news = []

    seen_links = set()
    seen_titles = set()

    for item in failed_items:

        link = item.get(
            "link",
            "",
        )

        title = item.get(
            "title",
            "",
        )

        if not link:
            continue

        normalized_link = db._normalize_url(
            link
        )

        normalized_title = _normalize_title(
            title
        )

        if normalized_link in seen_links:
            continue

        if (
            normalized_title
            and normalized_title in seen_titles
        ):
            continue

        if _is_filtered_title(
            title
        ):

            _record_filtered_title(
                title=title,
                link=link,
                source="Retry",
            )

            continue

        print(
            "\n♻️ إعادة محاولة خبر فاشل:"
        )

        print(
            f"   📰 {title[:180]}"
        )

        print(
            f"   🔗 {link}"
        )

        verified = _verify_article_page(
            link,
            cycle_start,
            FINAL_MAX_AGE_HOURS,
        )

        if not verified.get(
            "success"
        ):

            if verified.get(
                "retryable"
            ):

                print(
                    "   ⏳ ما زال الفشل مؤقتًا، "
                    "سيتم الاحتفاظ به لإعادة المحاولة."
                )

            else:

                print(
                    "   🚫 لم يعد الخبر صالحًا لإعادة المحاولة."
                )

            continue

        real_published_dt = verified[
            "published_dt"
        ]

        resolved_url = verified[
            "resolved_url"
        ]

        if not _is_recent(
            real_published_dt,
            cycle_start,
            FINAL_MAX_AGE_HOURS,
        ):

            print(
                "   ⏭️ انتهت صلاحية الخبر كخبر جديد "
                "لأنه أصبح خارج نافذة الـ3 ساعات."
            )

            continue

        seen_links.add(
            normalized_link
        )

        if normalized_title:
            seen_titles.add(
                normalized_title
            )

        retry_news.append(
            {
                "title": title,
                "link": link,
                "published": real_published_dt.isoformat(),
                "published_dt": real_published_dt,
                "source": "Kooora Retry",
                "matched_keyword": "Kooora",
                "resolved_url": resolved_url,
            }
        )

        print(
            "   ✅ تم استعادة الخبر الفاشل "
            "وأصبح جاهزًا لإعادة المعالجة."
        )

    print(
        f"♻️ الأخبار المستعادة من الفشل: "
        f"{len(retry_news)}"
    )

    return retry_news


# ============================================================
# Google News كخطة احتياطية
# ============================================================

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

            raw_data, _ = _fetch_url(
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

                normalized_title = (
                    _normalize_title(
                        clean_title
                    )
                )

                if link in seen_links:
                    continue

                if normalized_title in seen_titles:
                    continue

                if _is_filtered_title(
                    clean_title
                ):

                    _record_filtered_title(
                        title=clean_title,
                        link=link,
                        source=source_name,
                    )

                    seen_links.add(
                        link
                    )

                    continue

                if db.is_processed(
                    link,
                    clean_title,
                ):
                    continue

                published_parsed = (
                    entry.get(
                        "published_parsed"
                    )
                    or entry.get(
                        "updated_parsed"
                    )
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

                if "kooora.com" in (
                    urllib.parse.urlparse(
                        link
                    ).netloc.lower()
                ):

                    print(
                        "\n🔎 التحقق من خبر Google News "
                        "من صفحة كووورة الأصلية:"
                    )

                    print(
                        f"   📰 {clean_title[:180]}"
                    )

                    verified = _verify_article_page(
                        link,
                        cycle_start,
                        max_age,
                    )

                    if not verified.get(
                        "success"
                    ):

                        if verified.get(
                            "retryable"
                        ):

                            _save_retryable_failure(
                                link,
                                clean_title,
                            )

                        continue

                    published_dt = verified[
                        "published_dt"
                    ]

                    link = verified[
                        "resolved_url"
                    ]

                if not _is_recent(
                    published_dt,
                    cycle_start,
                    max_age,
                ):
                    continue

                seen_links.add(
                    link
                )

                seen_titles.add(
                    normalized_title
                )

                all_news.append(
                    {
                        "title": clean_title,
                        "link": link,
                        "published": published_dt.isoformat(),
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


# ============================================================
# الدالة الرئيسية
# ============================================================

def fetch_prioritized_news(
    cycle_start: datetime | None = None,
) -> list:

    _reset_title_filter_report()

    settings = CONFIG.get(
        "fetch_settings",
        {},
    )

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
        - timedelta(
            hours=FINAL_MAX_AGE_HOURS
        )
    )

    pages = settings.get(
        "kooora_pages",
        6,
    )

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
        "⏱️ نافذة الأخبار الجديدة: آخر 3 ساعات "
        "من وقت بدء الدورة."
    )

    print(
        "🔄 نافذة إعادة محاولة الأخبار الفاشلة: "
        "6 ساعات من وقت أول فشل."
    )

    print(
        "🔍 نافذة الفلترة الأولية من القائمة: "
        "آخر 9 ساعات."
    )

    print(
        "🚫 سيتم استبعاد أي عنوان يحتوي على "
        "«القنوات الناقلة» أو «القناة الناقلة»."
    )

    print(
        "ℹ️ الوقت الموجود في صفحة القائمة "
        "ليس وقت الاعتماد النهائي."
    )

    print(
        "ℹ️ سيتم فتح صفحة الخبر نفسها "
        "للتحقق من وقت النشر الحقيقي."
    )

    print(
        f"🕐 بداية الدورة: "
        f"{cycle_start.isoformat()}"
    )

    print(
        f"🕐 بداية نافذة الأخبار الجديدة: "
        f"{cutoff.isoformat()}"
    )

    retry_news = _retry_failed_news(
        cycle_start
    )

    direct_news = _fetch_kooora_direct(
        cycle_start,
        pages,
    )

    if direct_news:

        all_news = retry_news + direct_news

        print(
            f"✅ تم العثور على {len(direct_news)} "
            "خبرًا جديدًا حقيقيًا وحديثًا من كووورة."
        )

    else:

        print(
            "⚠️ لم يتم الحصول على أخبار مباشرة "
            "مقبولة من كووورة."
        )

        print(
            "🔄 الانتقال إلى Google News "
            "كخطة احتياطية..."
        )

        fallback_news = _fetch_google_news_fallback(
            cycle_start,
            FINAL_MAX_AGE_HOURS,
        )

        all_news = retry_news + fallback_news

    final_news = []

    seen_links = set()
    seen_titles = set()

    for news in all_news:

        published_dt = news.get(
            "published_dt"
        )

        if not published_dt:
            continue

        if not _is_recent(
            published_dt,
            cycle_start,
            FINAL_MAX_AGE_HOURS,
        ):
            continue

        link = news.get(
            "link",
            "",
        )

        title = news.get(
            "title",
            "",
        )

        normalized_link = db._normalize_url(
            link
        )

        normalized_title = _normalize_title(
            title
        )

        if normalized_link in seen_links:
            continue

        if (
            normalized_title
            and normalized_title in seen_titles
        ):
            continue

        seen_links.add(
            normalized_link
        )

        if normalized_title:
            seen_titles.add(
                normalized_title
            )

        final_news.append(
            news
        )

    final_news.sort(
        key=lambda x: x["published_dt"],
        reverse=True,
    )

    for news in final_news:

        news.pop(
            "published_dt",
            None,
        )

        news.pop(
            "list_published_dt",
            None,
        )

    filtered_count = len(
        _FILTERED_TITLE_ITEMS
    )

    print(
        "\n📊 النتيجة النهائية:"
    )

    print(
        f"📰 الأخبار المقبولة: "
        f"{len(final_news)}"
    )

    print(
        f"🚫 الأخبار المستبعدة بسبب العنوان: "
        f"{filtered_count}"
    )

    print(
        "⏱️ جميع الأخبار المقبولة تم التحقق "
        "من وقت نشرها من صفحة الخبر نفسها."
    )

    print(
        "🔄 الأخبار الفاشلة لا تضيع أثناء نافذة "
        "إعادة المحاولة البالغة 6 ساعات."
    )

    return final_news
