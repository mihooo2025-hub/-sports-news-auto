"""
config.py
=========
يحمّل الإعدادات بطريقتين حسب بيئة التشغيل:

1. من متغيرات البيئة (Environment Variables) — تُستخدم تلقائيًا عند التشغيل
   على GitHub Actions (تُقرأ بيانات الاعتماد من GitHub Secrets الآمنة، ولا
   تُكتب أبدًا داخل الملفات المرفوعة للمستودع).
2. من ملف config.json — تُستخدم عند التشغيل محليًا على Pydroid 3.

الأولوية دائمًا لمتغيرات البيئة إن وُجدت، وإلا يُقرأ config.json.
"""

import json
import os
import sys

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(
            "❌ لم يتم العثور على config.json\n"
            "➡️ انسخ config.example.json وأعد تسميته إلى config.json، "
            "ثم ضع بياناتك الحقيقية بداخله."
        )
        sys.exit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # تطغى متغيرات البيئة على قيم config.json الخاصة ببيانات الاعتماد الحساسة ورابط الموقع
    # (تشغيل عبر GitHub Actions لا يحتاج كتابة الأسرار أو الرابط داخل الملف نفسه).
    env_map = {
        ("wordpress", "site_url"): "WP_SITE_URL",
        ("wordpress", "username"): "WP_USERNAME",
        ("wordpress", "app_password"): "WP_APP_PASSWORD",
        ("openai", "api_key"): "OPENAI_API_KEY",
        ("telegram", "bot_token"): "TELEGRAM_BOT_TOKEN",
        ("telegram", "chat_id"): "TELEGRAM_CHAT_ID",
    }
    for (section, key), env_name in env_map.items():
        env_value = os.environ.get(env_name)
        if env_value:
            cfg.setdefault(section, {})[key] = env_value

    required_paths = [
        ("wordpress", "site_url"),
        ("wordpress", "username"),
        ("wordpress", "app_password"),
        ("openai", "api_key"),
    ]
    for section, key in required_paths:
        value = cfg.get(section, {}).get(key, "")
        if not value or "PASTE_YOUR" in value:
            print(f"❌ الحقل {section}.{key} غير معبّأ (لا في config.json ولا في متغيرات البيئة)")
            sys.exit(1)

    return cfg


CONFIG = load_config()
