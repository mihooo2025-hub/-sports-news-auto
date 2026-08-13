"""
wordpress_publisher.py
======================
يقوم بنشر المقالات المُصاغة إلى موقع ووردبريس عبر REST API:
- إنشاء المقال بنص HTML والتصنيفات المحددة.
- إرجاع رابط المقال المنشور المباشر على الموقع (link).
"""

import requests
from config import CONFIG

WP_CONFIG = CONFIG.get("wordpress", {})
WP_URL = WP_CONFIG.get("site_url", "").rstrip("/")
WP_USER = WP_CONFIG.get("username", "")

# القراءة المرنة لمفتاح كلمة مرور التطبيق لدعم المسميين (app_password و application_password)
WP_APP_PASSWORD = WP_CONFIG.get("app_password") or WP_CONFIG.get("application_password", "")


def publish_post(title: str, content: str, categories: list) -> str | None:
    """
    يقوم بنشر المقال إلى ووردبريس ويرجع رابط المقال المنشور على الموقع عند النجاح.
    """
    # التحقق من أن القيمة ليست فارغة وليست النص الافتراضي للتجربة
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

    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"

    # التأكد من هئية التصنيفات المقبولة لدى ووردبريس
    valid_categories = [cat for cat in categories if isinstance(cat, int)]

    payload = {
        "title": title,
        "content": content,
        "status": "publish",
    }
    
    if valid_categories:
        payload["categories"] = valid_categories

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
