"""
rss_fetcher.py
==============
يجلب أخبار كووورة مباشرة، مع Google News RSS كخطة احتياطية.

- المصدر الأساسي: صفحات أخبار كووورة.
- يفحص عدة صفحات من الأحدث إلى الأقدم.
- يستخدم وقت صفحة القائمة كفلتر أولي فقط.
- يتحقق من وقت النشر الحقيقي من صفحة الخبر نفسها.
- النافذة النهائية للأخبار المقبولة: آخر 3 ساعات.
- يمنع الأخبار التي لا يمكن تحديد وقت نشرها بدقة.
- يمنع التكرار عبر الرابط والعنوان في قاعدة البيانات.
"""

import html
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# يوجد فرق زمني ملحوظ أحيانًا بين وقت بطاقة الخبر
# ووقت الخبر الحقيقي، لذلك نستخدم نافذة أولية أوسع.
# النافذة النهائية تبقى 3 ساعات.
PREFILTER_MAX_AGE_HOURS = 9

# عدد الطلبات المتوازية إلى صفحات الأخبار نفسها.
# محدود لتجنب الضغط أو الحظر.
ARTICLE_TIME_WORKERS = 6

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
        وقت بداية الدورة ناقص max_age_hours.

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
    استخراج التاريخ والوقت من نص كووورة.

    يدعم:
        09:46 20 أغسطس 2026
        20 أغسطس 2026 09:46
        09:4620 أغسطس 2026
    """

    if not text:
        return None

    text = (
        str(text)
        .translate(ARABIC_DIGITS)
    )

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
        # HH:MMDD MONTH YYYY
        rf"(\d{{1,2}}):(\d{{2}})\s*"
        rf"(\d{{1,2}})\s*"
        rf"{month_pattern}\s*"
        rf"(\d{{4}})",

        # HH:MM DD MONTH YYYY
        rf"(\d{{1,2}}):(\d{{2}})\s+"
        rf"(\d{{1,2}})\s+"
        rf"{month_pattern}\s+"
        rf"(\d{{4}})",

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

            first_token = (
                match.group(0)
                .split()[0]
            )

            if ":" in first_token:
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
    استخراج وقت النشر من بطاقة الخبر نفسها.

    لا يعتمد على صفحة الخبر هنا.
    صفحة الخبر نفسها أصبحت المرجع النهائي لاحقًا.
    """

    if anchor is None:
        return None

    # 1) خصائص الرابط نفسه.
    parsed = _extract_datetime_from_node_attributes(
        anchor
    )

    if parsed:
        return parsed

    # 2) عناصر <time> داخل الرابط.
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

    # 3) النص الظاهر داخل الرابط.
    anchor_text = anchor.get_text(
        " ",
        strip=True,
    )

    parsed = _parse_visible_datetime(
        anchor_text
    )

    if parsed:
        return parsed

    # 4) خصائص العناصر الأب القريبة.
    parent = anchor.parent

    for _ in range(3):
        if parent is None:
            break

        parsed = _extract_datetime_from_node_attributes(
            parent
        )

        if parsed:
            return parsed

        # إذا كان الأب يحتوي على time واحد فقط.
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

    # 5) الأب المباشر فقط.
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


def _extract_article_page_time(
    raw_data: bytes,
):
    """
    استخراج وقت النشر الحقيقي من صفحة الخبر نفسها.

    الأولوية:
    1. meta tags.
    2. عناصر time.
    3. JSON-LD.
    4. __NEXT_DATA__.
    5. بيانات Next.js/RSC المضمنة.
    6. نص الصفحة إذا كان يحتوي تاريخًا بصيغة كووورة.
    """

    if not raw_data:
        return None

    soup = BeautifulSoup(
        raw_data,
        "html.parser",
    )

    # -------------------------------------------------
    # 1) meta tags
    # -------------------------------------------------
    for tag in soup.find_all("meta"):
        property_name = (
            tag.get("property")
            or tag.get("name")
            or tag.get("itemprop")
            or ""
        ).lower()

        if property_name in {
            "article:published_time",
            "og:published_time",
            "datepublished",
            "datepublishedtime",
            "published_time",
            "publishedtime",
            "published_at",
            "publishedat",
            "date",
        }:
            value = tag.get("content")

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
    # 2) عناصر <time>
    # -------------------------------------------------
    for time_tag in soup.find_all("time"):
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
    # 3) JSON-LD
    # -------------------------------------------------
    for script in soup.find_all(
        "script",
        type="application/ld+json",
    ):
        text = (
            script.string
            or script.get_text(
                strip=True
            )
        )

        if not text:
            continue

        try:
            data = __import__("json").loads(
                text
            )

            parsed = _extract_datetime_from_json(
                data
            )

            if parsed:
                return parsed

        except Exception:
            pass

    # -------------------------------------------------
    # 4) __NEXT_DATA__
    # -------------------------------------------------
    next_data = soup.find(
        "script",
        id="__NEXT_DATA__",
    )

    if next_data:
        text = (
            next_data.string
            or next_data.get_text(
                strip=True
            )
        )

        if text:
            try:
                data = __import__("json").loads(
                    text
                )

                parsed = _extract_datetime_from_json(
                    data
                )

                if parsed:
                    return parsed

            except Exception:
                pass

    # -------------------------------------------------
    # 5) بيانات Next.js/RSC
    # -------------------------------------------------
    raw_text = raw_data.decode(
        "utf-8",
        errors="ignore",
    )

    date_key_pattern = (
        r'(?:"|\\")'
        r'(?:datePublished|publishedAt|published_at|'
        r'publishedTime|publishedDate|dateCreated)'
        r'(?:"|\\")\s*:\s*'
        r'(?:"|\\")'
        r'([^"\\]+)'
        r'(?:"|\\")'
    )

    for match in re.finditer(
        date_key_pattern,
        raw_text,
        flags=re.IGNORECASE,
    ):
        value = match.group(1)

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
    # 6) البحث في نص صفحة الخبر.
    #
    # هذا مهم لأن وقت كووورة الحقيقي قد يظهر
    # كنص بجوار اسم الكاتب.
    # -------------------------------------------------
    page_text = soup.get_text(
        " ",
        strip=True,
    )

    parsed = _parse_visible_datetime(
        page_text
    )

    if parsed:
        return parsed

    return None


def _extract_datetime_from_json(value):
    """
    البحث داخل JSON المتداخل عن حقول تاريخ النشر.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = (
                str(key)
                .lower()
                .replace("-", "")
                .replace("_", "")
            )

            if normalized_key in {
                "datepublished",
                "publishedat",
                "publishedtime",
                "publisheddate",
                "datecreated",
            }:
                if isinstance(item, str):
                    parsed = _parse_datetime(
                        item
                    )

                    if parsed:
                        return parsed

                    parsed = _parse_visible_datetime(
                        item
                    )

                    if parsed:
                        return parsed

            parsed = _extract_datetime_from_json(
                item
            )

            if parsed:
                return parsed

    elif isinstance(value, list):
        for item in value:
            parsed = _extract_datetime_from_json(
                item
            )

            if parsed:
                return parsed

    return None


def _fetch_article_published_time(
    article_url: str,
):
    """
    جلب صفحة الخبر لاستخراج وقت النشر الحقيقي.
    """

    try:
        raw_data = _fetch_url(
            article_url,
            timeout=12,
        )

        published_dt = _extract_article_page_time(
            raw_data
        )

        return article_url, published_dt

    except Exception as e:
        print(
            "⚠️ تعذر فحص صفحة الخبر لاستخراج "
            "وقت النشر:"
        )
        print(
            f"   🔗 {article_url}"
        )
        print(
            f"   ⚠️ {e}"
        )

        return article_url, None


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

        if published_dt:
            parsed_times += 1
        else:
            missing_times += 1

        seen.add(link)

        results.append(
            {
                "title": title,
                "link": link,
                "published": (
                    published_dt.isoformat()
                    if published_dt
                    else ""
                ),
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

    الصفحات التالية:
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

    prefilter_cutoff = (
        cycle_start
        - timedelta(
            hours=PREFILTER_MAX_AGE_HOURS
        )
    )

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

            # -------------------------------------------------
            # فلتر أولي:
            #
            # لا نعتمد هنا على الـ3 ساعات.
            # نستخدم 9 ساعات فقط لتحديد الأخبار التي تستحق
            # فتح صفحة الخبر والتحقق من الوقت الحقيقي.
            #
            # الخبر الذي لا يملك وقتًا في القائمة يمر للفحص
            # من صفحة الخبر نفسها.
            # -------------------------------------------------
            candidates = []

            page_prefilter_old = 0
            page_missing = 0

            for item in page_news:
                link = item["link"]
                list_dt = item.get(
                    "published_dt"
                )

                if link in seen_links:
                    continue

                if list_dt:
                    if not (
                        prefilter_cutoff
                        <= list_dt
                        <= cycle_start
                    ):
                        page_prefilter_old += 1
                        continue

                else:
                    page_missing += 1

                candidates.append(item)

            # -------------------------------------------------
            # التحقق من الوقت الحقيقي من صفحات الأخبار.
            #
            # يتم فقط:
            # - للمرشحين ضمن 9 ساعات.
            # - أو الأخبار التي لم نستطع معرفة وقتها من القائمة.
            #
            # مع تنفيذ متوازٍ محدود.
            # -------------------------------------------------
            detail_times = {}

            if candidates:
                with ThreadPoolExecutor(
                    max_workers=ARTICLE_TIME_WORKERS
                ) as executor:
                    futures = {
                        executor.submit(
                            _fetch_article_published_time,
                            item["link"],
                        ): item["link"]
                        for item in candidates
                    }

                    for future in as_completed(
                        futures
                    ):
                        article_url = futures[
                            future
                        ]

                        try:
                            returned_url, published_dt = (
                                future.result()
                            )

                            detail_times[
                                returned_url
                            ] = published_dt

                        except Exception:
                            detail_times[
                                article_url
                            ] = None

            page_recent = 0
            page_old = 0
            page_processed = 0
            page_failed_time = 0

            for item in candidates:
                link = item["link"]
                title = item["title"]

                real_published_dt = detail_times.get(
                    link
                )

                # -------------------------------------------------
                # وقت صفحة الخبر هو المرجع النهائي.
                # -------------------------------------------------
                if not real_published_dt:
                    page_failed_time += 1

                    if page_failed_time <= 10:
                        print(
                            "⚠️ تعذر استخراج وقت النشر الحقيقي "
                            "من صفحة الخبر:"
                        )
                        print(
                            f"   🔗 {link}"
                        )
                        print(
                            f"   📰 {title[:180]}"
                        )

                    continue

                item["published_dt"] = (
                    real_published_dt
                )

                item["published"] = (
                    real_published_dt.isoformat()
                )

                # -------------------------------------------------
                # التحقق النهائي:
                # آخر 3 ساعات فقط.
                # -------------------------------------------------
                if not _is_recent(
                    real_published_dt,
                    cycle_start,
                    max_age,
                ):
                    page_old += 1
                    continue

                page_recent += 1

                normalized_title = re.sub(
                    r"\s+",
                    " ",
                    title.strip(),
                ).lower()

                if link in seen_links:
                    continue

                if normalized_title in seen_titles:
                    continue

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

                all_news.append(
                    item
                )

                print(
                    "✅ خبر اجتاز التحقق من صفحة "
                    "الخبر نفسها:"
                )
                print(
                    f"   📰 {title[:180]}"
                )
                print(
                    f"   🕐 {real_published_dt.isoformat()}"
                )

            print(
                f"📊 صفحة {page}: "
                f"articles={len(page_news)}, "
                f"candidates={len(candidates)}, "
                f"recent={page_recent}, "
                f"already_processed={page_processed}, "
                f"outside_window={page_old}, "
                f"prefilter_old={page_prefilter_old}, "
                f"missing_list_time={page_missing}, "
                f"failed_detail_time={page_failed_time}"
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

    # النافذة النهائية تبقى آخر 3 ساعات.
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
        "⏱️ نافذة الفحص النهائية: آخر 3 ساعات "
        "من وقت بدء الدورة."
    )

    print(
        f"🔎 الفلتر الأولي لوقت القائمة: "
        f"آخر {PREFILTER_MAX_AGE_HOURS} ساعات."
    )

    print(
        "ℹ️ الوقت النهائي المعتمد للخبر "
        "يُستخرج من صفحة الخبر نفسها."
    )

    print(
        f"🕐 بداية الدورة: "
        f"{cycle_start.isoformat()}"
    )

    print(
        f"🕐 بداية النافذة النهائية: "
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
