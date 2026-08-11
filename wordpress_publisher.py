"""
wordpress_publisher.py
=======================
- Validates credentials before processing (test_authentication).
- Uploads featured image to WordPress Media Library.
- Resolves category names to Category IDs via WordPress REST API.
  Never creates new categories — if no match is found, the category is omitted.
- Creates post draft with featured image and resolved categories.
"""

import requests
from requests.auth import HTTPBasicAuth
from config import CONFIG

# User-Agent محاكي لمتصفح حقيقي لتفادي حظر البوتات و Bot Verification 403
HEADERS_WP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
}

HEADERS_IMAGE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

_category_cache = {}


def _get_wp_credentials():
    wp = CONFIG.get("wordpress", {})
    site_url = wp.get("site_url", "").rstrip("/")
    auth = HTTPBasicAuth(wp.get("username", ""), wp.get("app_password", ""))
    return site_url, auth, wp.get("username", "")


def test_authentication() -> bool:
    """
    Validates WordPress credentials before processing to avoid wasting API quota.
    """
    site_url, auth, username = _get_wp_credentials()
    try:
        resp = requests.get(
            f"{site_url}/wp-json/wp/v2/users/me",
            auth=auth,
            headers=HEADERS_WP,
            timeout=10,
        )
        if resp.status_code == 200:
            user_data = resp.json()
            print(f"✅ WordPress authentication successful — User: {user_data.get('name', username)}")
            return True

        print(f"❌ WordPress authentication failed (Status code: {resp.status_code}).")
        if resp.status_code == 401:
            print(
                "Possible reasons:\n"
                "  1) Application Password expired/invalid — generate a new one.\n"
                "  2) Typo in credentials.\n"
                "  3) Username mismatch.\n"
                "  4) Server/Hosting stripping Authorization header."
            )
        else:
            print(f"Details: {resp.text[:300]}")
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

    try:
        resp = requests.get(
            f"{site_url}/wp-json/wp/v2/categories",
            params={"search": name, "per_page": 5},
            auth=auth,
            headers=HEADERS_WP,
            timeout=10,
        )
        resp.raise_for_status()
        for cat in resp.json():
            if cat["name"].strip() == name.strip():
                _category_cache[name] = cat["id"]
                return cat["id"]

        print(f"⚠️ Category '{name}' not found in WordPress — post will be created without it.")
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
        img_resp = requests.get(image_url, headers=HEADERS_IMAGE, timeout=15)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        if "image" not in content_type:
            print(f"⚠️ URL does not contain valid image data (Content-Type: {content_type})")
            return None

        ext = "jpg"
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"

        filename = f"featured-{abs(hash(image_url)) % 10**8}.{ext}"

        upload_resp = requests.post(
            f"{site_url}/wp-json/wp/v2/media",
            auth=auth,
            headers={
                "User-Agent": HEADERS_WP["User-Agent"],
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
                f"{site_url}/wp-json/wp/v2/media/{media_id}",
                auth=auth,
                json={"alt_text": alt_text},
                headers=HEADERS_WP,
                timeout=10,
            )

        return media_id

    except Exception as e:
        print(f"⚠️ Featured image upload failed: {e}")
        return None


def create_draft_post(ai_result: dict, source_url: str, image_url: str) -> dict | None:
    main_title = ai_result["title"]
    site_url, auth, _ = _get_wp_credentials()

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
            f"{site_url}/wp-json/wp/v2/posts",
            auth=auth,
            json=post_payload,
            headers=HEADERS_WP,
            timeout=15,
        )
        resp.raise_for_status()
        post_data = resp.json()
        print(f"✅ Draft created successfully: {post_data.get('link', post_data.get('id'))}")
        return post_data
    except Exception as e:
        print(f"❌ Failed to create WordPress post: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Error details: {e.response.text[:500]}")
        return None
