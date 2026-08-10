"""
wordpress_publisher.py
=======================
- يتحقق من صحة بيانات الاعتماد قبل أي معالجة (test_authentication).
- يرفع الصورة البارزة إلى مكتبة الوسائط.
- يحل أسماء التصنيفات إلى أرقام IDs عبر REST API (مع تخزين مؤقت محلي).
  لا يُنشئ أي تصنيف جديد أبدًا — إذا لم يجد تطابقًا، يُترك الخبر بلا هذا التصنيف.
- ينشئ المقال كمسودة (Draft) مع تعيين الصورة البارزة والتصنيفات.
"""

import requests
from requests.auth import HTTPBasicAuth
from config import CONFIG

WP = CONFIG["wordpress"]
SITE_URL = WP["site_url"].rstrip("/")
AUTH = HTTPBasicAuth(WP["username"], WP["app_password"])

HEADERS_JSON = {"Content-Type": "application/json"}
HEADERS_IMAGE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

_category_cache = {}


def test_authentication() -> bool:
    """
    يتحقق من صحة بيانات الاعتماد قبل بدء أي معالجة، لتفادي إهدار استدعاءات
    OpenAI (وتكلفتها) على مقالات ستفشل حتمًا عند محاولة نشرها لاحقًا.
    """
    try:
        resp = requests.get(f"{SITE_URL}/wp-json/wp/v2/users/me", auth=AUTH, timeout=10)
        if resp.status_code == 200:
            user_data = resp.json()
            print(f"✅ تم التحقق من الاتصال بووردبريس بنجاح — المستخدم: {user_data.get('name', WP['username'])}")
            return True

        print(f"❌ فشلت المصادقة مع ووردبريس (كود: {resp.status_code}).")
        if resp.status_code == 401:
            print(
                "الأسباب المحتملة:\n"
                "  1) كلمة مرور Application Password قديمة/ملغاة — أنشئ واحدة جديدة وحدّثها.\n"
                "  2) خطأ في النسخ (مسافة ناقصة أو حرف مفقود).\n"
                "  3) اسم المستخدم غير مطابق للاسم الفعلي في ووردبريس.\n"
                "  4) (نادر) الاستضافة تحذف رأس Authorization."
            )
        else:
            print(f"تفاصيل: {resp.text[:300]}")
        return False

    except Exception as e:
        print(f"❌ تعذر الاتصال بموقع ووردبريس إطلاقًا: {e}")
        return False


def _get_category_id(name: str) -> int | None:
    """
    يبحث عن تصنيف موجود فعليًا في ووردبريس بنفس الاسم تمامًا.
    لا يقوم بإنشاء أي تصنيف جديد مطلقًا — إذا لم يجد تطابقًا، يُعيد None
    ويُترك الخبر بلا هذا التصنيف بدل إنشاء تصنيف مكرر أو جديد.
    """
    if name in _category_cache:
        return _category_cache[name]

    try:
        resp = requests.get(
            f"{SITE_URL}/wp-json/wp/v2/categories",
            params={"search": name, "per_page": 5},
            auth=AUTH,
            timeout=10,
        )
        resp.raise_for_status()
        for cat in resp.json():
            if cat["name"].strip() == name.strip():
                _category_cache[name] = cat["id"]
                return cat["id"]

        print(f"⚠️ التصنيف '{name}' غير موجود فعليًا في ووردبريس — سيُنشر الخبر بدونه (لن يُنشأ تصنيف جديد).")
        return None

    except Exception as e:
        print(f"⚠️ فشل البحث عن التصنيف '{name}': {e}")

    return None


def resolve_category_ids(category_names: list) -> list:
    ids = []
    for name in category_names:
        cat_id = _get_category_id(name)
        if cat_id:
            ids.append(cat_id)
    return ids


def upload_featured_image(image_url: str, alt_text: str = "") -> int | None:
    if not image_url:
        return None

    try:
        img_resp = requests.get(image_url, headers=HEADERS_IMAGE, timeout=15)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        if "image" not in content_type:
            print(f"⚠️ الرابط لا يحتوي صورة صالحة (Content-Type: {content_type})")
            return None

        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"featured-{abs(hash(image_url)) % 10**8}.{ext}"

        upload_resp = requests.post(
            f"{SITE_URL}/wp-json/wp/v2/media",
            auth=AUTH,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Type": content_type,
            },
            data=img_resp.content,
            timeout=20,
        )
        upload_resp.raise_for_status()
        media_data = upload_resp.json()
        media_id = media_data["id"]

        if alt_text:
            requests.post(
                f"{SITE_URL}/wp-json/wp/v2/media/{media_id}",
                auth=AUTH,
                json={"alt_text": alt_text},
                headers=HEADERS_JSON,
                timeout=10,
            )

        return media_id

    except Exception as e:
        print(f"⚠️ فشل رفع الصورة البارزة: {e}")
        return None


def create_draft_post(ai_result: dict, source_url: str, image_url: str) -> dict | None:
    main_title = ai_result["title"]

    media_id = upload_featured_image(image_url, alt_text=main_title)
    category_ids = resolve_category_ids(ai_result.get("categories", []))

    post_payload = {
        "title": main_title,
        "content": ai_result["rewritten_content"],
        "status": "draft",
        "categories": category_ids,
    }
    if media_id:
        post_payload["featured_media"] = media_id

    try:
        resp = requests.post(
            f"{SITE_URL}/wp-json/wp/v2/posts",
            auth=AUTH,
            json=post_payload,
            headers=HEADERS_JSON,
            timeout=15,
        )
        resp.raise_for_status()
        post_data = resp.json()
        print(f"✅ تم إنشاء المسودة: {post_data.get('link', post_data.get('id'))}")
        return post_data
    except Exception as e:
        print(f"❌ فشل إنشاء المقال في ووردبريس: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"تفاصيل الخطأ: {e.response.text[:500]}")
        return None
