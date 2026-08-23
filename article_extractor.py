"""
article_extractor.py
=====================
يستقبل رابط مقال من كووورة مباشرة، يتحقق أنه ليس ضمن النطاقات الممنوعة،
ثم يستخرج من نفس الصفحة: نص المقال الكامل والصورة البارزة.
"""

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

BLOCKED_DOMAINS = [d.lower().lstrip("www.") for d in CONFIG.get("blocked_domains", [])]


def is_blocked_domain(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        netloc = netloc[4:] if netloc.startswith("www.") else netloc
        return any(netloc == blocked or netloc.endswith("." + blocked) for blocked in BLOCKED_DOMAINS)
    except Exception:
        return False


def _extract_image_from_soup(soup: BeautifulSoup, base_url: str) -> str | None:
    meta_candidates = [
        ("meta", {"property": "og:image:secure_url"}),
        ("meta", {"property": "og:image"}),
        ("meta", {"name": "twitter:image"}),
        ("meta", {"name": "twitter:image:src"}),
        ("link", {"rel": "image_src"}),
    ]
    for tag, attrs in meta_candidates:
        el = soup.find(tag, attrs=attrs)
        if el:
            src = el.get("content") or el.get("href")
            if src:
                return urljoin(base_url, src.strip())

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                img = item.get("image") if isinstance(item, dict) else None
                if img:
                    if isinstance(img, str):
                        return urljoin(base_url, img)
                    if isinstance(img, dict) and img.get("url"):
                        return urljoin(base_url, img["url"])
                    if isinstance(img, list) and img:
                        first = img[0]
                        if isinstance(first, str):
                            return urljoin(base_url, first)
                        if isinstance(first, dict) and first.get("url"):
                            return urljoin(base_url, first["url"])
        except Exception:
            continue

    container = soup.find("article") or soup.find("main") or soup.body
    if container:
        for img in container.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
            if not src:
                continue
            if any(bad in src.lower() for bad in ["logo", "icon", "avatar", "sprite", ".svg"]):
                continue
            return urljoin(base_url, src.strip())

    return None


def _extract_text_from_soup(soup: BeautifulSoup) -> str:
    # تنظيف عناصر الصفحة غير الضرورية
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    # استخراج النصوص من كافة الوسوم النصية (p, div, h1, h2, h3)
    paragraphs = []
    for el in container.find_all(["p", "div"]):
        # أخذ العناصر التي تحتوي نص مباشر بدون تعقيد
        text_content = el.get_text(strip=True)
        if len(text_content) > 15:
            paragraphs.append(text_content)

    # إزالة التكرارات مع الحفاظ على الترتيب
    seen = set()
    unique_paragraphs = []
    for p in paragraphs:
        if p not in seen:
            seen.add(p)
            unique_paragraphs.append(p)

    full_text = "\n".join(unique_paragraphs)
    return full_text.strip()


def extract_article(article_link: str, timeout: int = 12) -> dict:
    if is_blocked_domain(article_link):
        print(f"🚫 مصدر ممنوع — تم تجاوز الخبر بدون جلب محتواه: {article_link}")
        return {"resolved_url": article_link, "text": "", "image_url": None, "success": False, "blocked": True}

    try:
        resp = requests.get(article_link, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️ تعذر الوصول للرابط: {article_link} — {e}")
        return {"resolved_url": article_link, "text": "", "image_url": None, "success": False, "blocked": False}

    if is_blocked_domain(resp.url):
        print(f"🚫 مصدر ممنوع (بعد إعادة توجيه) — تم تجاوز الخبر: {resp.url}")
        return {"resolved_url": resp.url, "text": "", "image_url": None, "success": False, "blocked": True}

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = resp.url

    image_url = _extract_image_from_soup(soup, base_url)
    text = _extract_text_from_soup(soup)

    if not image_url:
        print(f"⚠️ تنبيه: لم يتم العثور على صورة للخبر: {article_link}")

    # خفض الحد الأدنى لطول النص المقبول إلى 40 حرفاً لمنع التجاوز غير الضروري
    has_sufficient_text = bool(text and len(text) >= 40)

    if not has_sufficient_text:
        print(f"⚠️ تنبيه: نص المقال قصير جدًا أو فارغ: {article_link}")

    return {
        "resolved_url": base_url,
        "text": text,
        "image_url": image_url,
        "success": has_sufficient_text,
        "blocked": False,
    }
