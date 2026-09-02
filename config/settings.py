import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
# DEBUG só em desenvolvimento local. homolog/production rodam com DEBUG=False
# (sem stack trace na resposta, CORS por lista explícita) — spec 2026-09-02 §10.5.
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
DEBUG = ENVIRONMENT == "development"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "corsheaders",
    "apps.optin",
    "apps.cliente",
    "apps.tenants",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Sem Django ORM — dados via shared.cloudsql_client (design §2/§3, Plan 03).
DATABASES = {}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# CORS — dev: qualquer localhost (vite muda de porta). Demais ambientes: só as
# origens listadas em CORS_ALLOWED_ORIGINS (separadas por vírgula).
if DEBUG:
    CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://localhost:\d+$", r"^http://127\.0\.0\.1:\d+$"]
    CORS_ALLOWED_ORIGINS = []
else:
    CORS_ALLOWED_ORIGIN_REGEXES = []
    CORS_ALLOWED_ORIGINS = [o for o in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",") if o]

from corsheaders.defaults import default_headers

CORS_ALLOW_HEADERS = list(default_headers) + ["idempotency-key"]
