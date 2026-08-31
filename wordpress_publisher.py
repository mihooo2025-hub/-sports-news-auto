"""
wordpress_publisher.py
=======================
- Validates credentials before processing (test_authentication).
- Uploads featured image to WordPress Media Library.
- Resolves category names to Category IDs via WordPress REST API.
  Never creates new categories — if no match is found, the category is omitted.
- Creates published post directly with featured image and resolved categories.
- Handles Cloudflare/WAF HTTP 403 responses without treating them as
  invalid WordPress credentials.
"""

import time
import requests
from requests.auth import HTTPBasicAuth
from config import CONFIG


# =========================================================
# ترويسات WordPress REST API
# =========================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

HEADERS_GET = DEFAULT_HEADERS.copy()

HEADERS_JSON = DEFAULT_HEADERS.copy()
HEADERS_JSON["Content-Type"] = "application/json"

# توافقية مع اسم المتغير القديم
HEADERS_WP = HEADERS_JSON

HEADERS_IMAGE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "image/avif,image/webp,image/apng,image/svg+xml,"
        "image/*,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

_category_cache = {}


# =========================================================
# إعدادات إعادة محاولة WordPress
# =========================================================

WP_MAX_RETRIES = 4

RETRY_STATUS_CODES = {
    403,
    429,
    500,
    502,
    503,
    504,
}

RETRY_DELAYS = [
    3,
    6,
    10,
]


# =========================================================
# إنشاء Session
# =========================================================

_session = requests.Session()

_session.headers.update(
    DEFAULT_HEADERS
)


# =========================================================
# طلبات WordPress مع إعادة المحاولة
# =========================================================

def _wp_request(
    method: str,
    url: str,
    *,
    auth=None,
    headers=None,
    timeout=15,
    **kwargs,
):
    """
    تنفيذ طلب إلى WordPress REST API مع إعادة المحاولة
    عند الأخطاء المؤقتة أو مشاكل الاتصال.

    HTTP 403 الناتج عن Cloudflare/WAF لا يتم اعتباره
    خطأ بيانات اعتماد بحد ذاته.
    """

    last_exception = None

    request_headers = DEFAULT_HEADERS.copy()

    if headers:
        request_headers.update(headers)

    for attempt in range(
        1,
        WP_MAX_RETRIES + 1,
    ):

        try:
            resp = _session.request(
                method,
                url,
                auth=auth,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=True,
                **kwargs,
            )

            # -------------------------------------------------
            # نجاح الطلب
            # -------------------------------------------------

            if resp.ok:
                return resp

            # -------------------------------------------------
            # أخطاء مؤقتة معروفة
            # -------------------------------------------------

            if resp.status_code in RETRY_STATUS_CODES:

                if attempt < WP_MAX_RETRIES:

                    delay = RETRY_DELAYS[
                        min(
                            attempt - 1,
                            len(RETRY_DELAYS) - 1,
                        )
                    ]

                    retry_after = resp.headers.get(
                        "Retry-After"
                    )

                    if retry_after:

                        try:
                            delay = max(
                                delay,
                                min(
                                    int(retry_after),
                                    30,
                                ),
                            )

                        except (
                            ValueError,
                            TypeError,
                        ):
                            pass

                    print(
                        f"⚠️ WordPress أعاد HTTP "
                        f"{resp.status_code} "
                        f"(المحاولة {attempt}/{WP_MAX_RETRIES})."
                    )

                    # -------------------------------------------------
                    # توضيح سبب 403 إذا كان Cloudflare/WAF
                    # -------------------------------------------------

                    if (
                        resp.status_code == 403
                        and _is_cloudflare_block(resp)
                    ):
                        print(
                            "🛡️ الطلب مرفوض بواسطة Cloudflare/WAF "
                            "وليس بسبب خطأ واضح في بيانات الدخول."
                        )

                    print(
                        f"🔄 إعادة المحاولة بعد "
                        f"{delay} ثوانٍ..."
                    )

                    time.sleep(
                        delay
                    )

                    continue

            # -------------------------------------------------
            # خطأ غير مؤقت
            # -------------------------------------------------

            return resp

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.RequestException,
        ) as e:

            last_exception = e

            if attempt < WP_MAX_RETRIES:

                delay = RETRY_DELAYS[
                    min(
                        attempt - 1,
                        len(RETRY_DELAYS) - 1,
                    )
                ]

                print(
                    f"⚠️ تعذر الاتصال بـ WordPress "
                    f"(المحاولة {attempt}/{WP_MAX_RETRIES}): {e}"
                )

                print(
                    f"🔄 إعادة المحاولة بعد "
                    f"{delay} ثوانٍ..."
                )

                time.sleep(
                    delay
                )

                continue

            break

    if last_exception:
        raise last_exception

    return None


# =========================================================
# اكتشاف Cloudflare / WAF
# =========================================================

def _is_cloudflare_block(resp) -> bool:

    if resp is None:
        return False

    server = (
        resp.headers.get("Server", "")
        or ""
    ).lower()

    cf_ray = (
        resp.headers.get("CF-Ray")
        or ""
    )

    cf_mitigated = (
        resp.headers.get("CF-Mitigated")
        or ""
    )

    cf_error_type = (
        resp.headers.get("cf-error-type")
        or ""
    )

    text = (
        resp.text[:5000]
        if hasattr(resp, "text")
        else ""
    ).lower()

    indicators = [
        "bot verification",
        "cloudflare",
        "cf-ray",
        "recaptcha",
        "challenge-platform",
        "managed challenge",
    ]

    return (
        "cloudflare" in server
        or bool(cf_ray)
        or bool(cf_mitigated)
        or bool(cf_error_type)
        or any(
            indicator in text
            for indicator in indicators
        )
    )


# =========================================================
# بيانات WordPress
# =========================================================

def _get_wp_credentials():

    wp = CONFIG.get(
        "wordpress",
        {},
    )

    site_url = wp.get(
        "site_url",
        "",
    ).rstrip("/")

    username = wp.get(
        "username",
        "",
    )

    password = wp.get(
        "app_password",
        "",
    )

    auth = HTTPBasicAuth(
        username,
        password,
    )

    return (
        site_url,
        auth,
        username,
    )


# =========================================================
# طباعة أخطاء WordPress
# =========================================================

def _print_wp_error(
    resp,
    action="WordPress",
):

    if resp is None:

        print(
            f"❌ {action} failed — No response"
        )

        return

    print(
        f"❌ {action} failed — HTTP "
        f"{resp.status_code}"
    )

    server = resp.headers.get(
        "Server"
    )

    cf_ray = resp.headers.get(
        "CF-Ray"
    )

    cf_mitigated = resp.headers.get(
        "CF-Mitigated"
    )

    cf_error_type = resp.headers.get(
        "cf-error-type"
    )

    cf_error_origin = resp.headers.get(
        "cf-error-origin"
    )

    if server:
        print(
            f"ℹ️ Server: {server}"
        )

    if cf_ray:
        print(
            f"ℹ️ CF-Ray: {cf_ray}"
        )

    if cf_mitigated:
        print(
            f"ℹ️ CF-Mitigated: {cf_mitigated}"
        )

    if cf_error_type:
        print(
            f"ℹ️ CF-Error-Type: {cf_error_type}"
        )

    if cf_error_origin:
        print(
            f"ℹ️ CF-Error-Origin: {cf_error_origin}"
        )

    # =====================================================
    # Cloudflare / WAF 403
    # =====================================================

    if (
        resp.status_code == 403
        and _is_cloudflare_block(resp)
    ):

        print(
            "🚫 تم رفض طلب WordPress REST API "
            "بواسطة طبقة حماية أمام الموقع "
            "(Cloudflare/WAF)."
        )

        print(
            "⚠️ هذا ليس دليلًا على أن Application Password "
            "خاطئة."
        )

        print(
            "ℹ️ يجب السماح بطلبات REST API من GitHub Actions "
            "في إعدادات الحماية."
        )

        print(
            "ℹ️ تمت إعادة المحاولة تلقائيًا قبل الوصول "
            "إلى هذه النتيجة."
        )

        print(
            f"Response: {resp.text[:500]}"
        )

        return

    # =====================================================
    # 403 عادي
    # =====================================================

    if resp.status_code == 403:

        print(
            "⚠️ WordPress returned 403 Forbidden."
        )

        print(
            "ℹ️ قد تكون المشكلة صلاحيات المستخدم "
            "أو إضافة أمنية."
        )

        print(
            f"Response: {resp.text[:500]}"
        )

        return

    # =====================================================
    # 525
    # =====================================================

    if resp.status_code == 525:

        print(
            "🚫 Cloudflare أعاد HTTP 525."
        )

        print(
            "⚠️ هذا يعني فشل اتصال SSL بين Cloudflare "
            "والخادم الأصلي."
        )

        print(
            f"Response: {resp.text[:500]}"
        )

        return

    # =====================================================
    # بقية الأخطاء
    # =====================================================

    print(
        f"Response: {resp.text[:500]}"
    )


# =========================================================
# اختبار المصادقة
# =========================================================

def test_authentication() -> bool:
    """
    التحقق من إمكانية استخدام WordPress REST API.

    401 فقط تعتبر مشكلة مصادقة واضحة.

    403 الناتجة عن Cloudflare/WAF لا تعتبر
    بيانات اعتماد خاطئة، ويُسمح للدورة بالاستمرار
    لمحاولة عمليات WordPress الفعلية.
    """

    site_url, auth, username = (
        _get_wp_credentials()
    )

    headers = HEADERS_GET.copy()

    # =====================================================
    # اختبار REST API الأساسي
    # =====================================================

    api_check = None

    try:

        api_check = _wp_request(
            "GET",
            f"{site_url}/wp-json/",
            headers=headers,
            timeout=15,
        )

    except Exception as e:

        print(
            f"⚠️ تعذر فحص REST API الأساسي: {e}"
        )

        print(
            "ℹ️ سيتم الانتقال مباشرة إلى اختبار المصادقة."
        )

    if (
        api_check is not None
        and api_check.status_code == 200
    ):

        print(
            "✅ WordPress REST API متاح."
        )

    elif (
        api_check is not None
        and api_check.status_code == 403
    ):

        print(
            "⚠️ REST API الأساسي أعاد HTTP 403 "
            "بعد إعادة المحاولة."
        )

        _print_wp_error(
            api_check,
            "WordPress REST API check",
        )

        print(
            "ℹ️ لن يتم إيقاف الدورة بسبب هذا الفحص."
        )

    elif api_check is not None:

        print(
            f"⚠️ فحص REST API أعاد HTTP "
            f"{api_check.status_code}."
        )

        print(
            "ℹ️ سيتم الاعتماد على اختبار المصادقة."
        )

    # =====================================================
    # الاختبار الحقيقي للمصادقة
    # =====================================================

    try:

        resp = _wp_request(
            "GET",
            f"{site_url}/wp-json/wp/v2/users/me",
            auth=auth,
            headers=headers,
            timeout=15,
        )

    except Exception as e:

        print(
            f"⚠️ تعذر الوصول إلى اختبار المصادقة: {e}"
        )

        print(
            "⚠️ قد يكون الخطأ مؤقتًا أو متعلقًا "
            "بطبقة الحماية."
        )

        print(
            "ℹ️ سيتم السماح للدورة بالاستمرار."
        )

        return True

    # =====================================================
    # المصادقة نجحت
    # =====================================================

    if (
        resp is not None
        and resp.status_code == 200
    ):

        try:
            user_data = resp.json()

        except Exception:
            user_data = {}

        print(
            f"✅ WordPress authentication successful — "
            f"User: {user_data.get('name', username)}"
        )

        return True

    # =====================================================
    # 401
    # =====================================================

    if (
        resp is not None
        and resp.status_code == 401
    ):

        _print_wp_error(
            resp,
            "WordPress authentication",
        )

        print(
            "❌ المصادقة مرفوضة بشكل واضح."
        )

        print(
            "Possible reasons:\n"
            "  1) Application Password expired/invalid — generate a new one.\n"
            "  2) Typo in credentials.\n"
            "  3) Username mismatch.\n"
            "  4) Server/Hosting stripping Authorization header."
        )

        return False

    # =====================================================
    # 403
    # =====================================================

    if (
        resp is not None
        and resp.status_code == 403
    ):

        _print_wp_error(
            resp,
            "WordPress authentication",
        )

        print(
            "⚠️ تعذر اختبار المصادقة بسبب HTTP 403."
        )

        print(
            "ℹ️ لن يتم اعتبار هذا فشلًا نهائيًا."
        )

        print(
            "🔄 ستتم محاولة عمليات WordPress الفعلية."
        )

        return True

    # =====================================================
    # أخطاء مؤقتة
    # =====================================================

    if (
        resp is not None
        and resp.status_code in {
            429,
            500,
            502,
            503,
            504,
            525,
        }
    ):

        _print_wp_error(
            resp,
            "WordPress authentication",
        )

        print(
            "⚠️ WordPress غير متاح مؤقتًا."
        )

        print(
            "ℹ️ لن يتم إيقاف الدورة بسبب "
            "الفحص المسبق."
        )

        return True

    # =====================================================
    # أي نتيجة أخرى
    # =====================================================

    if resp is not None:

        _print_wp_error(
            resp,
            "WordPress authentication",
        )

    print(
        "⚠️ تعذر تأكيد المصادقة مسبقًا."
    )

    print(
        "ℹ️ لن يتم إيقاف الدورة، "
        "وستتم محاولة عمليات WordPress الفعلية."
    )

    return True


# =========================================================
# البحث عن التصنيف
# =========================================================

def _get_category_id(
    name: str,
) -> int | None:

    if name in _category_cache:
        return _category_cache[name]

    site_url, auth, _ = (
        _get_wp_credentials()
    )

    headers = HEADERS_GET.copy()

    try:

        resp = _wp_request(
            "GET",
            f"{site_url}/wp-json/wp/v2/categories",
            params={
                "search": name,
                "per_page": 5,
            },
            auth=auth,
            headers=headers,
            timeout=15,
        )

        if resp is None:
            return None

        if not resp.ok:

            _print_wp_error(
                resp,
                f"Category search '{name}'",
            )

            return None

        for cat in resp.json():

            if (
                cat["name"].strip()
                == name.strip()
            ):

                _category_cache[name] = (
                    cat["id"]
                )

                return cat["id"]

        print(
            f"⚠️ Category '{name}' not found in WordPress — "
            f"post will be created without it."
        )

        return None

    except Exception as e:

        print(
            f"⚠️ Category search failed for "
            f"'{name}': {e}"
        )

    return None


# =========================================================
# تحويل أسماء التصنيفات إلى IDs
# =========================================================

def resolve_category_ids(
    category_names: list,
) -> list:

    category_names = (
        category_names or []
    )

    if not any(
        str(name).strip().lower()
        == "uncategorized"
        for name in category_names
    ):

        category_names = [
            "Uncategorized"
        ] + list(category_names)

    ids = []

    for name in category_names:

        cat_id = _get_category_id(
            name
        )

        if cat_id:
            ids.append(
                cat_id
            )

    return ids


# =========================================================
# رفع الصورة المميزة
# =========================================================

def upload_featured_image(
    image_url: str,
    alt_text: str = "",
) -> int | None:

    if not image_url:
        return None

    site_url, auth, _ = (
        _get_wp_credentials()
    )

    try:

        # -------------------------------------------------
        # تنزيل الصورة من المصدر
        # -------------------------------------------------

        img_resp = _session.get(
            image_url,
            headers=HEADERS_IMAGE,
            timeout=20,
            allow_redirects=True,
        )

        img_resp.raise_for_status()

        content_type = img_resp.headers.get(
            "Content-Type",
            "image/jpeg",
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

        filename = (
            f"featured-"
            f"{abs(hash(image_url)) % 10**8}"
            f".{ext}"
        )

        upload_headers = (
            DEFAULT_HEADERS.copy()
        )

        upload_headers.update({
            "Content-Disposition":
                f'attachment; filename="{filename}"',
            "Content-Type":
                content_type,
            "Accept":
                "application/json",
        })

        # -------------------------------------------------
        # رفع الصورة
        # -------------------------------------------------

        upload_resp = _wp_request(
            "POST",
            f"{site_url}/wp-json/wp/v2/media",
            auth=auth,
            headers=upload_headers,
            data=img_resp.content,
            timeout=30,
        )

        if (
            upload_resp is None
            or not upload_resp.ok
        ):

            if upload_resp is not None:

                _print_wp_error(
                    upload_resp,
                    "Featured image upload",
                )

            print(
                "🚫 لم يتم رفع الصورة المميزة."
            )

            print(
                "🚫 لن يتم نشر المقال بدون الصورة المميزة."
            )

            return None

        media_data = upload_resp.json()

        media_id = media_data["id"]

        # -------------------------------------------------
        # تحديث alt text
        # -------------------------------------------------

        if alt_text:

            alt_headers = (
                HEADERS_JSON.copy()
            )

            alt_resp = _wp_request(
                "POST",
                f"{site_url}/wp-json/wp/v2/media/{media_id}",
                auth=auth,
                json={
                    "alt_text": alt_text
                },
                headers=alt_headers,
                timeout=10,
            )

            if (
                alt_resp is not None
                and not alt_resp.ok
            ):

                _print_wp_error(
                    alt_resp,
                    "Featured image alt text update",
                )

        print(
            f"🖼️ تم رفع الصورة المميزة بنجاح "
            f"(Media ID: {media_id})"
        )

        return media_id

    except Exception as e:

        print(
            f"⚠️ Featured image upload failed: {e}"
        )

        print(
            "🚫 لن يتم نشر المقال بدون الصورة المميزة."
        )

        return None


# =========================================================
# إنشاء المقال
# =========================================================

def create_draft_post(
    ai_result: dict,
    source_url: str,
    image_url: str,
) -> dict | None:

    main_title = ai_result["title"]

    site_url, auth, _ = (
        _get_wp_credentials()
    )

    media_id = upload_featured_image(
        image_url,
        alt_text=main_title,
    )

    if image_url and not media_id:

        print(
            "🚫 تم إيقاف نشر المقال لأن "
            "الصورة المميزة لم تُرفع بنجاح."
        )

        return None

    category_ids = (
        resolve_category_ids(
            ai_result.get(
                "categories",
                [],
            )
        )
    )

    post_payload = {
        "title": main_title,
        "content": ai_result[
            "rewritten_content"
        ],
        "status": "publish",
        "categories": category_ids,
    }

    if media_id:

        post_payload[
            "featured_media"
        ] = media_id

    headers = HEADERS_JSON.copy()

    try:

        resp = _wp_request(
            "POST",
            f"{site_url}/wp-json/wp/v2/posts",
            auth=auth,
            json=post_payload,
            headers=headers,
            timeout=20,
        )

        if (
            resp is None
            or not resp.ok
        ):

            if resp is not None:

                _print_wp_error(
                    resp,
                    "Create WordPress post",
                )

            return None

        post_data = resp.json()

        print(
            f"✅ Article published successfully: "
            f"{post_data.get('link', post_data.get('id'))}"
        )

        return post_data

    except Exception as e:

        print(
            f"❌ Failed to create WordPress post: {e}"
        )

        if (
            hasattr(e, "response")
            and e.response is not None
        ):

            print(
                f"Error details: "
                f"{e.response.text[:500]}"
            )

        return None


# =========================================================
# الدالة التي يستدعيها main.py
# =========================================================

def publish_post(
    title: str,
    content: str,
    categories: list = None,
    image_url: str = None,
) -> str | None:

    """
    الدالة التي يتم استدعاؤها من main.py لنشر المقال
    وإرجاع رابط المنشور عند النجاح.
    """

    site_url, auth, _ = (
        _get_wp_credentials()
    )

    # -----------------------------------------------------
    # رفع الصورة قبل إنشاء المقال.
    # -----------------------------------------------------

    media_id = (
        upload_featured_image(
            image_url,
            alt_text=title,
        )
        if image_url
        else None
    )

    # -----------------------------------------------------
    # إذا كانت هناك صورة من المصدر وفشل رفعها،
    # لا يتم نشر المقال.
    # -----------------------------------------------------

    if image_url and not media_id:

        print(
            "🚫 فشل رفع الصورة المميزة."
        )

        print(
            "🚫 تم إلغاء نشر المقال مؤقتًا "
            "حتى لا يتم نشره بصورة الموقع الافتراضية."
        )

        return None

    category_ids = (
        resolve_category_ids(
            categories
        )
    )

    post_payload = {
        "title": title,
        "content": content,
        "status": "publish",
        "categories": category_ids,
    }

    if media_id:

        post_payload[
            "featured_media"
        ] = media_id

    headers = HEADERS_JSON.copy()

    try:

        resp = _wp_request(
            "POST",
            f"{site_url}/wp-json/wp/v2/posts",
            auth=auth,
            json=post_payload,
            headers=headers,
            timeout=20,
        )

        if (
            resp is None
            or not resp.ok
        ):

            if resp is not None:

                _print_wp_error(
                    resp,
                    "Create WordPress post",
                )

            return None

        post_data = resp.json()

        post_link = post_data.get(
            "link"
        )

        print(
            f"✅ Article published successfully: "
            f"{post_link or post_data.get('id')}"
        )

        return post_link

    except Exception as e:

        print(
            f"❌ Failed to create WordPress post: {e}"
        )

        if (
            hasattr(e, "response")
            and e.response is not None
        ):

            print(
                f"Error details: "
                f"{e.response.text[:500]}"
            )

        return None
