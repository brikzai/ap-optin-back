# optin-service — Plan 01: Django Project Scaffold — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Django project skeleton (no ORM) with a working health endpoint — the base every other plan builds on.

**Architecture:** Django with `DATABASES = {}` (no Django ORM/migrations, per house convention — see design doc). Single app `apps.optin`. Function-based views, JSON responses.

**Tech Stack:** Python 3.12, Django >=4.2,<5.0, djangorestframework (JSON renderer only), python-dotenv, pytest, pytest-django, gunicorn.

**Spec:** `docs/superpowers/specs/2026-08-18-optin-service-design.md` (§2). Series: plan 1 of 7 (`optin-plan-01` .. `optin-plan-07`), each independently reviewable/testable.

## Global Constraints

- `TIME_ZONE = "America/Sao_Paulo"`, `USE_TZ = True`.
- Secrets never committed; `.env` is git-ignored, `.env.example` holds only keys.
- No Django ORM: `DATABASES = {}` — data access goes through `shared/cloudsql_client.py` (Plan 03), not through this plan.

---

### Task 1: Django project scaffold

**Files:**
- Create: `optin/manage.py`
- Create: `optin/config/__init__.py`
- Create: `optin/config/settings.py`
- Create: `optin/config/urls.py`
- Create: `optin/config/wsgi.py`
- Create: `optin/apps/__init__.py`
- Create: `optin/apps/optin/__init__.py`
- Create: `optin/apps/optin/views.py`
- Create: `optin/apps/optin/urls.py`
- Create: `optin/requirements.txt`
- Create: `optin/.env.example`
- Create: `optin/.gitignore`
- Create: `optin/Dockerfile`
- Create: `optin/pytest.ini`
- Test: `optin/apps/optin/tests/test_health.py`

**Interfaces:**
- Produces: `GET /api/v1/health` → `200 {"status": "ok"}`. Every later plan's URLs mount under `config.urls` → `apps.optin.urls`.

- [ ] **Step 1: Write `requirements.txt`**

```
django>=4.2,<5.0
djangorestframework>=3.15
python-dotenv
sqlalchemy>=2.0
pg8000
cloud-sql-python-connector[pg8000]
google-cloud-secret-manager
httpx
python-ulid
pytest
pytest-django
respx
gunicorn
```

- [ ] **Step 2: Write `.gitignore`**

```
.venv/
__pycache__/
*.pyc
.env
staticfiles/
logs/
*.pem
```

- [ ] **Step 3: Write `.env.example`**

```
ENVIRONMENT=development
DJANGO_SECRET_KEY=dev-secret-key-change-in-production
ALLOWED_HOSTS=*

# Local Postgres (docker-compose) — deixe vazio em produção
LOCAL_DATABASE_URL=postgresql+pg8000://optin:optin@localhost:5433/optin

# Cloud SQL (produção/homolog) — usados só quando LOCAL_DATABASE_URL está vazio
CLOUDSQL_CONNECTION_NAME=
CLOUDSQL_DB_USER=
CLOUDSQL_DB_PASSWORD=
CLOUDSQL_DB_NAME=

# JWT do IdP corporativo (API interna)
IAM_JWT_PUBLIC_KEY=
IAM_JWT_ISSUER=brikz-iam

# CERC (homologação) — ver docs/superpowers/specs/2026-08-18-optin-service-design.md §5
CERC_CLIENT_ID=
CERC_CLIENT_SECRET=
CERC_AUTH_URL=https://api.int.cerc.com/oauth/token
CERC_API_BASE_URL=https://ap-homolog.cerc.inf.br
```

- [ ] **Step 4: Write `config/settings.py`**

```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
DEBUG = os.getenv("ENVIRONMENT", "development").lower() != "production"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "apps.optin",
]

MIDDLEWARE = [
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
```

- [ ] **Step 5: Write `config/urls.py`**

```python
from django.urls import path, include

urlpatterns = [
    path("api/v1/", include("apps.optin.urls")),
]
```

- [ ] **Step 6: Write `config/wsgi.py`**

```python
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
```

- [ ] **Step 7: Write `config/__init__.py`, `apps/__init__.py`, `apps/optin/__init__.py`**

Empty files, all three.

- [ ] **Step 8: Write `apps/optin/views.py`**

```python
from django.http import JsonResponse


def health(request):
    return JsonResponse({"status": "ok"})
```

- [ ] **Step 9: Write `apps/optin/urls.py`**

```python
from django.urls import path
from . import views

urlpatterns = [
    path("health", views.health),
]
```

- [ ] **Step 10: Write `manage.py`**

```python
#!/usr/bin/env python
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
```

- [ ] **Step 11: Write `pytest.ini`**

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings
python_files = tests.py test_*.py *_tests.py
```

- [ ] **Step 12: Write the failing test**

```python
# optin/apps/optin/tests/test_health.py
import pytest
from django.test import Client


@pytest.mark.django_db
def test_health_returns_ok():
    response = Client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Create empty `optin/apps/optin/tests/__init__.py` alongside it.

- [ ] **Step 13: Install deps and run test**

Run: `pip install -r requirements.txt` then `pytest apps/optin/tests/test_health.py -v`
Expected: PASS (this is a scaffold smoke test — there is no prior "red" state to observe since `health` has no logic to break; verifying it passes on first run confirms the scaffold is wired correctly).

- [ ] **Step 14: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8080
CMD exec gunicorn config.wsgi:application --bind :$PORT --workers 4
```

- [ ] **Step 15: Commit**

```bash
cd optin
git add manage.py config apps requirements.txt .env.example .gitignore Dockerfile pytest.ini
git commit -m "feat: scaffold Django project (no ORM), health endpoint"
```

---

## Self-Review Notes

- **Spec coverage:** design §2 (folder layout, health endpoint, no-ORM settings) → fully covered.
- **Placeholder scan:** none — every step has runnable code.
- **Type consistency:** N/A (first plan in the series — nothing to be consistent with yet). Exports `config.urls`/`config.settings` module paths that every later plan's `INSTALLED_APPS`/`urlpatterns` entries build on.

**Next:** `2026-08-19-optin-plan-02-schema.md` (local Postgres + SPEC-01 schema).
