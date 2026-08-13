"""
wordpress_publisher.py
======================
يقوم بنشر المقالات المُصاغة إلى موقع ووردبريس عبر REST API:
- رفع الصورة البارزة (Featured Media) وتعيينها للمقال.
- إنشاء المقال بنص HTML والتصنيفات المحددة.
- إرجاع رابط المقال المنشور المباشر على الموقع (link).
"""

import requests
from config import CONFIG

WP_CONFIG = CONFIG.get("wordpress", {})
WP_URL = WP_CONFIG.get("site_url", "").rstrip("/")
WP_USER = WP_CONFIG.get("username", "")
WP_APP_PASSWORD = WP_CONFIG.get("app_password") or WP_CONFIG.get("application_password", "")


def upload_featured_image(image_url: str) -> int | None:
    """رفع الصورة إلى ووردبريس وإرجاع الـ Media ID الخاص بها"""
    if not image_url:
        return None

    try:
        # تحميل الصورة من مصدرها
        img_resp = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if img_resp.status_code != 200:
            return None

        # استخراج اسم الصورة ونوعها
        file_name = image_url.split("/")[-1].split("?")[0]
        if not file_name.endswith((".jpg", ".jpeg", ".png", ".webp")):
            file_name = "featured_image.jpg"

        content_type = img_resp.headers.get("Content-Type", "image/jpeg")

        # رفع الصورة إلى ووردبريس REST API
        media_endpoint = f"{WP_URL}/wp-json/wp/v2/media"
        headers = {
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Content-Type": content_type,
        }

        response = requests.post(
            media_endpoint,
            data=img_resp.content,
            headers=headers,
            auth=(WP_USER, WP_APP_PASSWORD),
            timeout=30,
        )

        if response.status_code in (200, 201):
            return response.json().get("id")
        else:
            print(f"⚠️ فشل رفع الصورة لـ WP: {response.status_code}")
            return None

    except Exception as e:
        print(f"⚠️ خطأ أثناء رفع الصورة البارزة: {e}")
        return None


def publish_post(title: str, content: str, categories: list, image_url: str = None) -> str | None:
    """
    يقوم بنشر المقال إلى ووردبريس ويرجع رابط المقال المنشور على الموقع عند النجاح.
    """
    is_invalid = (
        not WP_URL 
        or not WP_USER 
        or not WP_APP_PASSWORD 
        or "PASTE_YOUR" in WP_USER 
        or "PASTE_YOUR" in WP_APP_PASSWORD 
        or "example.com" in WP_URL
    )

    if is_invalid:
        print("❌ بيانات اعتماد ووردبريس غير مكتملة أو تحتوي على القيم الافتراضية في config.json")
        return None

    # رفع الصورة أولاً إذا كانت متوفرة
    featured_media_id = upload_featured_image(image_url) if image_url else None

    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    valid_categories = [cat for cat in categories if isinstance(cat, int)]

    payload = {
        "title": title,
        "content": content,
        "status": "publish",
    }
    
    if valid_categories:
        payload["categories"] = valid_categories

    if featured_media_id:
        payload["featured_media"] = featured_media_id

    try:
        response = requests.post(
            endpoint,
            json=payload,
            auth=(WP_USER, WP_APP_PASSWORD),
            headers={"Content-Type": "application/json"},
            timeout=30,
        )

        if response.status_code in (200, 201):
            post_data = response.json()
            published_url = post_data.get("link", "")
            return published_url
        else:
            print(f"❌ فشل نشر المقال في ووردبريس: {response.status_code} - {response.text}")
            return None

    except Exception as e:
        print(f"⚠️ خطأ أثناء الاتصال بـ REST API لووردبريس: {e}")
        return None
