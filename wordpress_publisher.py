"""
wordpress_publisher.py
=======================
- Validates credentials before processing (test_authentication).
- Uploads featured image to WordPress Media Library.
- Resolves category names to Category IDs via WordPress REST API.
  Never creates new categories — if no match is found, the category is omitted.
- Creates published post directly with featured image and resolved categories.
"""

import requests
from requests.auth import HTTPBasicAuth
from config import CONFIG

# ترويسات بسيطة ومناسبة لـ WordPress REST API
DEFAULT_HEADERS = {
    "User-Agent": "news-bot/1.0",
    "Accept": "application/json, text/plain, */*",
}

HEADERS_GET = DEFAULT_HEADERS.copy()

HEADERS_JSON = DEFAULT_HEADERS.copy()
HEADERS_JSON["Content-Type"] = "application/json"

# توافقية مع اسم المتغير القديم
HEADERS_WP = HEADERS_JSON

HEADERS_IMAGE = {
    "User-Agent": "news-bot/1.0",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

_category_cache = {}


def _get_wp_credentials():
    wp = CONFIG.get("wordpress", {})
    site_url = wp.get("site_url", "").rstrip("/")
    username = wp.get("username", "")
    password = wp.get("app_password", "")

    auth = HTTPBasicAuth(username, password)

    return site_url, auth, username


def _print_wp_error(resp, action="WordPress"):
    print(f"❌ {action} failed — HTTP {resp.status_code}")

    # عرض معلومات مفيدة لمعرفة هل الرد جاء من Cloudflare/WAF
    server = resp.headers.get("Server")
    cf_ray = resp.headers.get("CF-Ray")
    cf_mitigated = resp.headers.get("CF-Mitigated")

    if server:
        print(f"ℹ️ Server: {server}")

    if cf_ray:
        print(f"ℹ️ CF-Ray: {cf_ray}")

    if cf_mitigated:
        print(f"ℹ️ CF-Mitigated: {cf_mitigated}")

    if resp.status_code == 403:
        if (
            "Bot Verification" in resp.text
            or "Cloudflare" in resp.text
            or "cf-" in resp.text.lower()
            or cf_ray
            or cf_mitigated
        ):
            print(
                "🚫 تم رفض طلب WordPress REST API بواسطة طبقة حماية "
                "أمام الموقع (مثل Cloudflare/WAF)."
            )
            print(
                "⚠️ هذا يعني غالبًا أن الطلب من GitHub Actions يصل إلى "
                "خادم الحماية قبل أن يصل إلى WordPress."
            )
            print(
                "➡️ راجع إعدادات حماية الموقع للسماح بطلبات REST API "
                "الشرعية الخاصة بالموقع."
            )
        else:
            print(
                "⚠️ WordPress returned 403 Forbidden. "
                "قد تكون المشكلة صلاحيات المستخدم أو إضافة أمنية."
            )

        print(f"Response: {resp.text[:500]}")

    else:
        print(f"Response: {resp.text[:500]}")


def test_authentication() -> bool:
    """
    Validates WordPress credentials before processing to avoid wasting API quota.
    كما يتحقق أولًا من إمكانية الوصول إلى REST API نفسه.
    """
    site_url, auth, username = _get_wp_credentials()

    headers = HEADERS_GET.copy()

    try:
        # ---------------------------------------------------------
        # 1) اختبار REST API بدون مصادقة
        # ---------------------------------------------------------
        api_check = requests.get(
            f"{site_url}/wp-json/",
            headers=headers,
            timeout=15,
        )

        if api_check.status_code == 200:
            print("✅ WordPress REST API متاح.")

        elif api_check.status_code == 403:
            print(
                "❌ REST API نفسه محجوب بدون حتى الوصول إلى مرحلة المصادقة."
            )
            _print_wp_error(api_check, "WordPress REST API check")
            return False

        else:
            print(
                f"⚠️ فحص REST API أعاد HTTP {api_check.status_code}."
            )

        # ---------------------------------------------------------
        # 2) اختبار المصادقة
        # ---------------------------------------------------------
        resp = requests.get(
            f"{site_url}/wp-json/wp/v2/users/me",
            auth=auth,
            headers=headers,
            timeout=15,
        )

        if resp.status_code == 200:
            user_data = resp.json()
            print(
                f"✅ WordPress authentication successful — User: "
                f"{user_data.get('name', username)}"
            )
            return True

        _print_wp_error(resp, "WordPress authentication")

        if resp.status_code == 401:
            print(
                "Possible reasons:\n"
                "  1) Application Password expired/invalid — generate a new one.\n"
                "  2) Typo in credentials.\n"
                "  3) Username mismatch.\n"
                "  4) Server/Hosting stripping Authorization header."
            )

        elif resp.status_code == 403:
            print(
                "Possible reasons:\n"
                "  1) Cloudflare/WAF/hosting security blocked the request.\n"
                "  2) Security plugin blocked REST API authentication.\n"
                "  3) The WordPress user lacks the required permissions.\n"
                "  4) Authorization header is being removed by the server/proxy."
            )

        return False

    except Exception as e:
        print(f"❌ Connection to WordPress failed: {e}")
        return False


def _get_category_id(name: str) -> int | None:
    """
    Looks up an existing category ID by name.
    Does not create new categories — returns None if not found.
    """
    if name in _category_cache:
        return _category_cache[name]

    site_url, auth, _ = _get_wp_credentials()

    headers = HEADERS_GET.copy()

    try:
        resp = requests.get(
            f"{site_url}/wp-json/wp/v2/categories",
            params={"search": name, "per_page": 5},
            auth=auth,
            headers=headers,
            timeout=15,
        )

        if not resp.ok:
            _print_wp_error(resp, f"Category search '{name}'")
            return None

        for cat in resp.json():
            if cat["name"].strip() == name.strip():
                _category_cache[name] = cat["id"]
                return cat["id"]

        print(
            f"⚠️ Category '{name}' not found in WordPress — "
            f"post will be created without it."
        )
        return None

    except Exception as e:
        print(f"⚠️ Category search failed for '{name}': {e}")

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

    site_url, auth, _ = _get_wp_credentials()

    try:
        img_resp = requests.get(
            image_url,
            headers=HEADERS_IMAGE,
            timeout=15,
        )
        img_resp.raise_for_status()

        content_type = img_resp.headers.get(
            "Content-Type",
            "image/jpeg"
        )

        if "image" not in content_type:
            print(
                f"⚠️ URL does not contain valid image data "
                f"(Content-Type: {content_type})"
            )
            return None

        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"featured-{abs(hash(image_url)) % 10**8}.{ext}"

        upload_headers = DEFAULT_HEADERS.copy()
        upload_headers.update({
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": content_type,
        })

        upload_resp = requests.post(
            f"{site_url}/wp-json/wp/v2/media",
            auth=auth,
            headers=upload_headers,
            data=img_resp.content,
            timeout=20,
        )

        if not upload_resp.ok:
            _print_wp_error(upload_resp, "Featured image upload")
            return None

        media_data = upload_resp.json()
        media_id = media_data["id"]

        if alt_text:
            alt_headers = HEADERS_JSON.copy()

            alt_resp = requests.post(
                f"{site_url}/wp-json/wp/v2/media/{media_id}",
                auth=auth,
                json={"alt_text": alt_text},
                headers=alt_headers,
                timeout=10,
            )

            if not alt_resp.ok:
                _print_wp_error(
                    alt_resp,
                    "Featured image alt text update"
                )

        return media_id

    except Exception as e:
        print(f"⚠️ Featured image upload failed: {e}")
        return None


def create_draft_post(
    ai_result: dict,
    source_url: str,
    image_url: str
) -> dict | None:
    main_title = ai_result["title"]
    site_url, auth, _ = _get_wp_credentials()

    media_id = upload_featured_image(
        image_url,
        alt_text=main_title
    )

    category_ids = resolve_category_ids(
        ai_result.get("categories", [])
    )

    post_payload = {
        "title": main_title,
        "content": ai_result["rewritten_content"],
        "status": "publish",
        "categories": category_ids,
    }

    if media_id:
        post_payload["featured_media"] = media_id

    headers = HEADERS_JSON.copy()

    try:
        resp = requests.post(
            f"{site_url}/wp-json/wp/v2/posts",
            auth=auth,
            json=post_payload,
            headers=headers,
            timeout=15,
        )

        if not resp.ok:
            _print_wp_error(resp, "Create WordPress post")
            return None

        post_data = resp.json()

        print(
            f"✅ Article published successfully: "
            f"{post_data.get('link', post_data.get('id'))}"
        )

        return post_data

    except Exception as e:
        print(f"❌ Failed to create WordPress post: {e}")

        if hasattr(e, "response") and e.response is not None:
            print(
                f"Error details: "
                f"{e.response.text[:500]}"
            )

        return None


def publish_post(
    title: str,
    content: str,
    categories: list = None,
    image_url: str = None
) -> str | None:
    """
    الدالة التي يتم استدعاؤها من main.py لنشر المقال
    وإرجاع رابط المنشور عند النجاح.
    """
    site_url, auth, _ = _get_wp_credentials()

    media_id = (
        upload_featured_image(
            image_url,
            alt_text=title
        )
        if image_url
        else None
    )

    category_ids = (
        resolve_category_ids(categories)
        if categories
        else []
    )

    post_payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": category_ids,
    }

    if media_id:
        post_payload["featured_media"] = media_id

    headers = HEADERS_JSON.copy()

    try:
        resp = requests.post(
            f"{site_url}/wp-json/wp/v2/posts",
            auth=auth,
            json=post_payload,
            headers=headers,
            timeout=15,
        )

        if not resp.ok:
            _print_wp_error(resp, "Create WordPress post")
            return None

        post_data = resp.json()
        post_link = post_data.get("link")

        print(
            f"✅ Article published successfully: "
            f"{post_link or post_data.get('id')}"
        )

        return post_link

    except Exception as e:
        print(f"❌ Failed to create WordPress post: {e}")

        if hasattr(e, "response") and e.response is not None:
            print(
                f"Error details: "
                f"{e.response.text[:500]}"
            )

        return None
