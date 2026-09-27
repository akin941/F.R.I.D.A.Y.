import os

from django.core.wsgi import get_wsgi_application

# Production by default (Phase 4 branch 1): a server started without an
# explicit DJANGO_SETTINGS_MODULE fails hard on a missing environment
# (config/settings/prod.py) instead of booting with DEBUG=True. Every dev
# path names config.settings.dev explicitly (compose, Makefile).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_wsgi_application()
