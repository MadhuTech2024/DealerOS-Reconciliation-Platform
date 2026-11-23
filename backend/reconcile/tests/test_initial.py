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
