"""
article_extractor.py
=====================
يحل رابط Google News إلى الرابط الأصلي، يتحقق أنه ليس ضمن النطاقات الممنوعة،
ثم يستخرج من نفس الصفحة: نص المقال الكامل (لأي لغة — تتم ترجمته لاحقًا في
content_ai.py) والصورة البارزة.
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


# --------------------------------------------------------------------------
# حل رابط Google News
# --------------------------------------------------------------------------
def resolve_google_news_url(gnews_url: str, timeout: int = 10) -> str:
    try:
        match = re.search(r"/articles/([^?]+)", gnews_url)
        if match:
            encoded = match.group(1)
            padded = encoded + "=" * (-len(encoded) % 4)
            decoded = base64.urlsafe_b64decode(padded)
            url_match = re.search(rb"https?://[^\x00-\x1f\"']+", decoded)
            if url_match:
                candidate = url_match.group(0).decode("utf-8", errors="ignore")
                if "google.com" not in candidate:
                    return candidate
    except Exception:
        pass

    try:
        resp = requests.get(gnews_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        final_url = resp.url
        if "news.google.com" not in final_url:
            return final_url
        soup = BeautifulSoup(resp.text, "lxml")
        meta_refresh = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
        if meta_refresh and meta_refresh.get("content"):
            m = re.search(r"url=(\S+)", meta_refresh["content"], re.I)
            if m:
                return m.group(1).strip("'\"")
        c_wiz = soup.find("c-wiz")
        if c_wiz:
            article = c_wiz.find("a")
            if article and article.get("href"):
                return article["href"]
    except Exception:
        pass

    try:
        from googlenewsdecoder import new_decoderv1
        result = new_decoderv1(gnews_url)
        if result.get("status") and result.get("decoded_url"):
            return result["decoded_url"]
    except ImportError:
        pass
    except Exception:
        pass

    return gnews_url


# --------------------------------------------------------------------------
# استخراج الصورة البارزة من صفحة تم تحميلها بالفعل
# --------------------------------------------------------------------------
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
            width = img.get("width")
            if width and width.isdigit() and int(width) < 200:
                continue
            return urljoin(base_url, src.strip())

    return None


# --------------------------------------------------------------------------
# استخراج نص المقال من صفحة تم تحميلها بالفعل
# --------------------------------------------------------------------------
def _extract_text_from_soup(soup: BeautifulSoup) -> str:
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return ""

    for tag in container.find_all(["script", "style", "nav", "footer", "aside", "form"]):
        tag.decompose()

    paragraphs = container.find_all("p")
    text = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))
    text = "\n".join(line for line in text.split("\n") if len(line) > 25)
    return text.strip()


# --------------------------------------------------------------------------
# الدالة الرئيسية المجمّعة
# --------------------------------------------------------------------------
def extract_article(gnews_link: str, timeout: int = 12) -> dict:
    """
    يُعيد dict فيها:
    {
        "resolved_url": الرابط الأصلي,
        "text": نص المقال الكامل (بأي لغة)،
        "image_url": رابط الصورة البارزة أو None,
        "success": True/False,
        "blocked": True إذا كان المصدر ضمن النطاقات الممنوعة
    }
    """
    real_url = resolve_google_news_url(gnews_link, timeout=timeout)

    if is_blocked_domain(real_url):
        print(f"🚫 مصدر ممنوع — تم تجاوز الخبر بدون جلب محتواه: {real_url}")
        return {"resolved_url": real_url, "text": "", "image_url": None, "success": False, "blocked": True}

    try:
        resp = requests.get(real_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        print(f"⚠️ تعذر الوصول للرابط الأصلي: {real_url} — {e}")
        return {"resolved_url": real_url, "text": "", "image_url": None, "success": False, "blocked": False}

    # تحقق ثانٍ من النطاق بعد أي إعادة توجيه إضافية حدثت أثناء هذا الطلب
    if is_blocked_domain(resp.url):
        print(f"🚫 مصدر ممنوع (بعد إعادة توجيه) — تم تجاوز الخبر: {resp.url}")
        return {"resolved_url": resp.url, "text": "", "image_url": None, "success": False, "blocked": True}

    soup = BeautifulSoup(resp.text, "lxml")
    base_url = resp.url

    image_url = _extract_image_from_soup(soup, base_url)
    text = _extract_text_from_soup(soup)

    if not image_url:
        print(f"⚠️ تنبيه: لم يتم العثور على صورة للخبر: {real_url}")

    if not text or len(text) < 100:
        print(f"⚠️ تنبيه: نص المقال قصير جدًا أو فارغ: {real_url}")

    return {
        "resolved_url": base_url,
        "text": text,
        "image_url": image_url,
        "success": bool(text and len(text) > 100),
        "blocked": False,
    }
