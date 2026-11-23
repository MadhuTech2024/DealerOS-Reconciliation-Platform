import os
from pathlib import Path
import subprocess
import sys

import pytest
from django.conf import settings
from django.apps import apps

@pytest.mark.django_db
def test_django_setup():
    """Verify that Django settings are loaded properly."""
    assert settings.INSTALLED_APPS is not None
    assert 'reconcile' in settings.INSTALLED_APPS
    assert 'rest_framework' in settings.INSTALLED_APPS
    assert 'corsheaders' in settings.INSTALLED_APPS

def test_cors_settings():
    """Verify that CORS is configured for Vite dev server."""
    assert 'http://localhost:5173' in settings.CORS_ALLOWED_ORIGINS
    assert 'http://127.0.0.1:5173' in settings.CORS_ALLOWED_ORIGINS
    assert settings.CORS_ALLOW_CREDENTIALS is True

def test_reconcile_app_registered():
    """Verify that the reconcile app is registered in Django's app registry."""
    app_config = apps.get_app_config('reconcile')
    assert app_config.name == 'reconcile'


def _run_production_settings(secret_key=None, debug=None):
    backend_dir = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    for key in ("DJANGO_ENV", "DJANGO_SECRET_KEY", "DJANGO_DEBUG"):
        env.pop(key, None)
    env["DJANGO_ENV"] = "production"
    if secret_key is not None:
        env["DJANGO_SECRET_KEY"] = secret_key
    if debug is not None:
        env["DJANGO_DEBUG"] = debug
    return subprocess.run(
        [sys.executable, "-c", "import config.settings; print(config.settings.DEBUG)"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_production_settings_require_secret_and_default_debug_off():
    missing_secret = _run_production_settings()
    assert missing_secret.returncode != 0
    assert "DJANGO_SECRET_KEY is required in production" in missing_secret.stderr

    valid_secret = _run_production_settings(secret_key="test-only-" + ("x" * 64))
    assert valid_secret.returncode == 0, valid_secret.stderr
    assert valid_secret.stdout.strip() == "False"


def test_production_settings_reject_debug_true():
    result = _run_production_settings(
        secret_key="test-only-" + ("x" * 64),
        debug="true",
    )
    assert result.returncode != 0
    assert "DJANGO_DEBUG must be false" in result.stderr
