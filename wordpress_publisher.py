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
WP_APP_PASSWORD = WP_CONFIG.get("application_password", "")


def publish_post(title: str, content: str, categories: list) -> str | None:
    """
    يقوم بنشر المقال إلى ووردبريس ويرجع رابط المقال المنشور على الموقع عند النجاح.
    """
    if not WP_URL or not WP_USER or not WP_APP_PASSWORD:
        print("❌ بيانات اعتماد ووردبريس غير مكتملة في config.json")
        return None

    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"

    payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": categories,
    }

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
