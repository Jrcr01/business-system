"""WSGI entry point (for the server, Phase F3)."""
import os
import sys
from pathlib import Path

ERP_DIR = Path(__file__).resolve().parent.parent
for p in (str(ERP_DIR), str(ERP_DIR.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
