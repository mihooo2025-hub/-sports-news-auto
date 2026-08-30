"""
article_extractor.py
=====================
يحل رابط Google News إلى الرابط الأصلي، يتحقق أنه ليس ضمن النطاقات الممنوعة،
ثم يستخرج من نفس الصفحة: نص المقال الكامل والصورة البارزة.
"""

import re
import base64
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from config import CONFIG


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/webp,*/*;q=0.8"
    ),
}


BLOCKED_DOMAINS = [
    d.lower().lstrip("www.")
    for d in CONFIG.get("blocked_domains", [])
]


def is_blocked_domain(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc[4:] if netloc.startswith("www.") else netloc

        return any(
            netloc == blocked
            or netloc.endswith("." + blocked)
            for blocked in BLOCKED_DOMAINS
        )

    except Exception:
        return False


def resolve_google_news_url(
    gnews_url: str,
    timeout: int = 10
) -> str:

    try:
        match = re.search(
            r"/articles/([^?]+)",
            gnews_url
        )

        if match:
            encoded = match.group(1)

            padded = encoded + "=" * (
                -len(encoded) % 4
            )

            decoded = base64.urlsafe_b64decode(
                padded
            )

            url_match = re.search(
                rb"https?://[^\x00-\x1f\"']+",
                decoded
            )

            if url_match:
                candidate = url_match.group(
                    0
                ).decode(
                    "utf-8",
                    errors="ignore"
                )

                if "google.com" not in candidate:
                    return candidate

    except Exception:
        pass


    try:
        resp = requests.get(
            gnews_url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True
        )

        final_url = resp.url

        if "news.google.com" not in final_url:
            return final_url

        soup = BeautifulSoup(
            resp.text,
            "lxml"
        )

        meta_refresh = soup.find(
            "meta",
            attrs={
                "http-equiv": re.compile(
                    "refresh",
                    re.I
                )
            }
        )

        if (
            meta_refresh
            and meta_refresh.get("content")
        ):
            m = re.search(
                r"url=(\S+)",
                meta_refresh["content"],
                re.I
            )

            if m:
                return m.group(
                    1
                ).strip("'\"")


        c_wiz = soup.find("c-wiz")

        if c_wiz:
            article = c_wiz.find("a")

            if (
                article
                and article.get("href")
            ):
                return article.get("href")

    except Exception:
        pass


    try:
        from googlenewsdecoder import new_decoderv1

        result = new_decoderv1(
            gnews_url
        )

        if (
            result.get("status")
            and result.get("decoded_url")
        ):
            return result["decoded_url"]

    except ImportError:
        pass

    except Exception:
        pass


    return gnews_url


# =========================================================
# أدوات استخراج الصور
# =========================================================

def _is_kooora_url(url: str) -> bool:
    """
    التحقق من أن الرابط تابع لكووورة.
    """

    try:
        hostname = urlparse(url).netloc.lower()

        hostname = hostname[4:] if hostname.startswith(
            "www."
        ) else hostname

        return (
            hostname == "kooora.com"
            or hostname.endswith(".kooora.com")
        )

    except Exception:
        return False


def _is_bad_image_url(url: str) -> bool:
    """
    يستبعد الصور العامة التي لا تصلح كصورة للمقال.
    """

    if not url:
        return True

    value = url.lower().strip()

    bad_keywords = [
        "logo",
        "kooora-logo",
        "kooora_logo",
        "koooraicon",
        "favicon",
        "icon",
        "avatar",
        "sprite",
        "placeholder",
        "default-image",
        "default_image",
        "defaultimage",
        "no-image",
        "no_image",
        "noimage",
        "loading",
        "spinner",
        "banner",
        "background",
        "header-logo",
        "footer-logo",
    ]

    if any(
        keyword in value
        for keyword in bad_keywords
    ):
        return True

    if value.endswith(".svg"):
        return True

    return False


def _clean_image_url(
    src: str,
    base_url: str
) -> str | None:
    """
    تنظيف وتحويل رابط الصورة إلى رابط مطلق.
    """

    if not src:
        return None

    src = str(src).strip()

    if not src:
        return None

    if src.startswith(
        "data:image"
    ):
        return None

    # إزالة بعض الصيغ غير المفيدة
    if src.startswith(
        "javascript:"
    ):
        return None

    try:
        image_url = urljoin(
            base_url,
            src
        )

        if _is_bad_image_url(
            image_url
        ):
            return None

        return image_url

    except Exception:
        return None


def _extract_srcset_url(
    srcset: str
) -> str | None:
    """
    استخراج أفضل رابط من srcset.
    """

    if not srcset:
        return None

    candidates = []

    for item in srcset.split(","):
        item = item.strip()

        if not item:
            continue

        parts = item.split()

        if not parts:
            continue

        url = parts[0]

        width = 0

        if len(parts) > 1:
            match = re.search(
                r"(\d+)(?:w|x)",
                parts[1]
            )

            if match:
                try:
                    width = int(
                        match.group(1)
                    )
                except Exception:
                    width = 0

        candidates.append(
            (
                width,
                url
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0][1]


def _get_img_url(
    img,
    base_url: str
) -> str | None:
    """
    استخراج رابط الصورة من جميع الصيغ الشائعة
    المستخدمة في التحميل العادي والكسول.
    """

    attributes = [
        "src",
        "data-src",
        "data-original",
        "data-lazy-src",
        "data-original-src",
        "data-image",
        "data-image-url",
        "data-url",
    ]

    for attr in attributes:
        value = img.get(attr)

        if value:
            image_url = _clean_image_url(
                value,
                base_url
            )

            if image_url:
                return image_url


    srcset_attributes = [
        "srcset",
        "data-srcset",
        "data-lazy-srcset",
    ]

    for attr in srcset_attributes:
        value = img.get(attr)

        if value:
            best = _extract_srcset_url(
                value
            )

            if best:
                image_url = _clean_image_url(
                    best,
                    base_url
                )

                if image_url:
                    return image_url

    return None


def _image_score(
    img,
    image_url: str,
    article_title: str = ""
) -> int:
    """
    إعطاء درجة لكل صورة لتحديد الصورة الأقرب
    إلى الصورة البارزة للمقال.
    """

    score = 0

    if not image_url:
        return -1000


    url_lower = image_url.lower()


    # -----------------------------------------------------
    # استبعاد الصور العامة بشكل قوي
    # -----------------------------------------------------

    if _is_bad_image_url(
        image_url
    ):
        return -1000


    # -----------------------------------------------------
    # الصور الموجودة داخل المقال لها أولوية
    # -----------------------------------------------------

    parent = img.parent

    if parent:
        parent_name = (
            parent.name or ""
        ).lower()

        parent_class = " ".join(
            parent.get(
                "class",
                []
            )
        ).lower()

        parent_id = str(
            parent.get(
                "id",
                ""
            )
        ).lower()

        context = (
            parent_class
            + " "
            + parent_id
        )

        if parent_name in [
            "article",
            "figure"
        ]:
            score += 30

        if any(
            word in context
            for word in [
                "article",
                "story",
                "news",
                "content",
                "featured",
                "hero",
                "main-image",
                "main_image",
                "cover",
            ]
        ):
            score += 25


    # -----------------------------------------------------
    # اسم الصورة أو alt
    # -----------------------------------------------------

    alt = str(
        img.get("alt", "")
    ).lower()

    title = str(
        img.get("title", "")
    ).lower()

    image_context = (
        alt
        + " "
        + title
    )

    if image_context.strip():
        score += 10


    # إذا كان alt قريبًا من عنوان الخبر
    if article_title:
        normalized_title = re.sub(
            r"\s+",
            " ",
            article_title.lower()
        ).strip()

        normalized_alt = re.sub(
            r"\s+",
            " ",
            alt
        ).strip()

        if (
            normalized_alt
            and normalized_title
            and (
                normalized_alt in normalized_title
                or normalized_title in normalized_alt
            )
        ):
            score += 35


    # -----------------------------------------------------
    # بيانات الأبعاد
    # -----------------------------------------------------

    width = img.get(
        "width"
    )

    height = img.get(
        "height"
    )

    try:
        if width:
            width = int(
                re.sub(
                    r"[^\d]",
                    "",
                    str(width)
                )
            )

        if height:
            height = int(
                re.sub(
                    r"[^\d]",
                    "",
                    str(height)
                )
            )

        if width and width >= 500:
            score += 15

        if height and height >= 250:
            score += 10

        if (
            width
            and height
            and height > 0
        ):
            ratio = width / height

            if 1.2 <= ratio <= 2.2:
                score += 10

    except Exception:
        pass


    # -----------------------------------------------------
    # الصور الكبيرة في روابط CDN
    # -----------------------------------------------------

    if any(
        keyword in url_lower
        for keyword in [
            "image",
            "images",
            "media",
            "upload",
            "uploads",
            "cdn",
            "photo",
            "photos",
        ]
    ):
        score += 5


    # -----------------------------------------------------
    # الصور التي تحمل اسمًا واضحًا لصورة خبر
    # -----------------------------------------------------

    if any(
        keyword in url_lower
        for keyword in [
            "article",
            "story",
            "news",
            "featured",
            "cover",
        ]
    ):
        score += 10


    # -----------------------------------------------------
    # منع الصور التي تبدو صغيرة جدًا
    # -----------------------------------------------------

    if width and width < 200:
        score -= 30

    if height and height < 100:
        score -= 30


    return score


def _extract_kooora_image(
    soup: BeautifulSoup,
    base_url: str,
    article_title: str = ""
) -> str | None:
    """
    استخراج صورة المقال من كووورة.

    كووورة قد تستخدم صورًا يتم تحميلها بطريقة lazy loading،
    لذلك لا نعتمد على src فقط.
    """

    candidates = []


    # =====================================================
    # 1) الصور الموجودة داخل article
    # =====================================================

    article_containers = []

    for selector in [
        "article",
        '[class*="article"]',
        '[class*="story"]',
        '[class*="news"]',
        '[class*="content"]',
        "main",
    ]:
        try:
            elements = soup.select(
                selector
            )

            for element in elements:
                if element not in article_containers:
                    article_containers.append(
                        element
                    )

        except Exception:
            continue


    for container in article_containers:

        for img in container.find_all(
            "img"
        ):
            image_url = _get_img_url(
                img,
                base_url
            )

            if not image_url:
                continue

            score = _image_score(
                img,
                image_url,
                article_title
            )

            # الصور داخل كووورة نفسها لها أفضلية
            if _is_kooora_url(
                image_url
            ):
                score += 5

            candidates.append(
                (
                    score,
                    image_url
                )
            )


    # =====================================================
    # 2) البحث في جميع الصور إذا لم نجد مرشحًا قويًا
    # =====================================================

    for img in soup.find_all(
        "img"
    ):

        image_url = _get_img_url(
            img,
            base_url
        )

        if not image_url:
            continue

        score = _image_score(
            img,
            image_url,
            article_title
        )

        candidates.append(
            (
                score,
                image_url
            )
        )


    # =====================================================
    # 3) إزالة التكرارات
    # =====================================================

    unique = {}

    for score, image_url in candidates:

        if (
            image_url not in unique
            or score > unique[image_url]
        ):
            unique[image_url] = score


    candidates = [
        (
            score,
            image_url
        )
        for image_url, score
        in unique.items()
    ]


    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )


    # =====================================================
    # 4) اختيار أفضل صورة حقيقية
    # =====================================================

    if candidates:

        best_score, best_url = candidates[0]

        if best_score >= 10:
            return best_url


    return None


def _extract_image_from_soup(
    soup: BeautifulSoup,
    base_url: str,
    article_title: str = ""
) -> str | None:

    # =====================================================
    # كووورة
    # =====================================================

    if _is_kooora_url(
        base_url
    ):

        kooora_image = _extract_kooora_image(
            soup,
            base_url,
            article_title
        )

        if kooora_image:
            return kooora_image


    # =====================================================
    # Meta images
    # =====================================================

    meta_candidates = [
        (
            "meta",
            {
                "property": "og:image:secure_url"
            }
        ),
        (
            "meta",
            {
                "property": "og:image"
            }
        ),
        (
            "meta",
            {
                "name": "twitter:image"
            }
        ),
        (
            "meta",
            {
                "name": "twitter:image:src"
            }
        ),
        (
            "link",
            {
                "rel": "image_src"
            }
        ),
    ]


    for tag, attrs in meta_candidates:

        el = soup.find(
            tag,
            attrs=attrs
        )

        if el:

            src = (
                el.get("content")
                or el.get("href")
            )

            if src:

                image_url = _clean_image_url(
                    src.strip(),
                    base_url
                )

                if image_url:
                    return image_url


    # =====================================================
    # JSON-LD
    # =====================================================

    for script in soup.find_all(
        "script",
        type="application/ld+json"
    ):

        try:

            raw = (
                script.string
                or script.get_text()
            )

            if not raw:
                continue

            data = json.loads(
                raw
            )

            items = (
                data
                if isinstance(
                    data,
                    list
                )
                else [data]
            )

            for item in items:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                img = item.get(
                    "image"
                )

                if not img:
                    continue


                if isinstance(
                    img,
                    str
                ):

                    image_url = _clean_image_url(
                        img,
                        base_url
                    )

                    if image_url:
                        return image_url


                if isinstance(
                    img,
                    dict
                ):

                    image_src = (
                        img.get("url")
                        or img.get(
                            "contentUrl"
                        )
                    )

                    if image_src:

                        image_url = _clean_image_url(
                            image_src,
                            base_url
                        )

                        if image_url:
                            return image_url


                if isinstance(
                    img,
                    list
                ):

                    for first in img:

                        if isinstance(
                            first,
                            str
                        ):

                            image_url = _clean_image_url(
                                first,
                                base_url
                            )

                            if image_url:
                                return image_url


                        elif isinstance(
                            first,
                            dict
                        ):

                            image_src = (
                                first.get("url")
                                or first.get(
                                    "contentUrl"
                                )
                            )

                            if image_src:

                                image_url = _clean_image_url(
                                    image_src,
                                    base_url
                                )

                                if image_url:
                                    return image_url

        except Exception:
            continue


    # =====================================================
    # البحث العام داخل article / main / body
    # =====================================================

    container = (
        soup.find("article")
        or soup.find("main")
        or soup.body
    )

    if container:

        for img in container.find_all(
            "img"
        ):

            src = _get_img_url(
                img,
                base_url
            )

            if not src:
                continue

            if _is_bad_image_url(
                src
            ):
                continue

            return src


    return None


# =========================================================
# استخراج نص المقال
# =========================================================

def _extract_text_from_soup(
    soup: BeautifulSoup
) -> str:

    # تنظيف عناصر الصفحة غير الضرورية
    for tag in soup.find_all(
        [
            "script",
            "style",
            "nav",
            "footer",
            "header",
            "aside",
            "form",
            "noscript",
        ]
    ):
        tag.decompose()


    container = (
        soup.find("article")
        or soup.find("main")
        or soup.body
    )

    if not container:
        return ""


    # استخراج النصوص من كافة الوسوم النصية
    paragraphs = []

    for el in container.find_all(
        [
            "p",
            "div"
        ]
    ):

        text_content = el.get_text(
            strip=True
        )

        if len(
            text_content
        ) > 15:

            paragraphs.append(
                text_content
            )


    # إزالة التكرارات مع الحفاظ على الترتيب
    seen = set()
    unique_paragraphs = []

    for p in paragraphs:

        if p not in seen:

            seen.add(p)

            unique_paragraphs.append(
                p
            )


    full_text = "\n".join(
        unique_paragraphs
    )

    return full_text.strip()


# =========================================================
# الدالة الرئيسية
# =========================================================

def extract_article(
    gnews_link: str,
    timeout: int = 12
) -> dict:

    real_url = resolve_google_news_url(
        gnews_link,
        timeout=timeout
    )


    if is_blocked_domain(
        real_url
    ):

        print(
            f"🚫 مصدر ممنوع — تم تجاوز الخبر "
            f"بدون جلب محتواه: {real_url}"
        )

        return {
            "resolved_url": real_url,
            "text": "",
            "image_url": None,
            "success": False,
            "blocked": True,
        }


    try:

        resp = requests.get(
            real_url,
            headers=HEADERS,
            timeout=timeout,
            allow_redirects=True
        )

        resp.raise_for_status()

    except Exception as e:

        print(
            f"⚠️ تعذر الوصول للرابط الأصلي: "
            f"{real_url} — {e}"
        )

        return {
            "resolved_url": real_url,
            "text": "",
            "image_url": None,
            "success": False,
            "blocked": False,
        }


    if is_blocked_domain(
        resp.url
    ):

        print(
            f"🚫 مصدر ممنوع (بعد إعادة توجيه) "
            f"— تم تجاوز الخبر: {resp.url}"
        )

        return {
            "resolved_url": resp.url,
            "text": "",
            "image_url": None,
            "success": False,
            "blocked": True,
        }


    soup = BeautifulSoup(
        resp.text,
        "html.parser"
    )

    base_url = resp.url


    # =====================================================
    # الحصول على عنوان المقال لاستخدامه في تقييم الصور
    # =====================================================

    article_title = ""

    title_tag = soup.find(
        "h1"
    )

    if title_tag:

        article_title = title_tag.get_text(
            " ",
            strip=True
        )

    if not article_title:

        og_title = soup.find(
            "meta",
            attrs={
                "property": "og:title"
            }
        )

        if og_title:

            article_title = (
                og_title.get(
                    "content",
                    ""
                )
            )


    # =====================================================
    # استخراج الصورة
    # =====================================================

    image_url = _extract_image_from_soup(
        soup,
        base_url,
        article_title
    )


    # =====================================================
    # استخراج النص
    # =====================================================

    text = _extract_text_from_soup(
        soup
    )


    if not image_url:

        print(
            f"⚠️ تنبيه: لم يتم العثور على صورة "
            f"للخبر: {real_url}"
        )

    else:

        print(
            f"🖼️ تم العثور على صورة المقال: "
            f"{image_url}"
        )


    # خفض الحد الأدنى لطول النص المقبول
    # إلى 40 حرفاً لمنع التجاوز غير الضروري
    has_sufficient_text = bool(
        text
        and len(text) >= 40
    )


    if not has_sufficient_text:

        print(
            f"⚠️ تنبيه: نص المقال قصير جدًا "
            f"أو فارغ: {real_url}"
        )


    return {
        "resolved_url": base_url,
        "text": text,
        "image_url": image_url,
        "success": has_sufficient_text,
        "blocked": False,
    }
