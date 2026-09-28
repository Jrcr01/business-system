#!/usr/bin/env python
"""Slicks ERP (Django) -- run from the ERP environment: .venv\\Scripts\\python erp\\manage.py <command>."""
import os
import sys
from pathlib import Path

ERP_DIR = Path(__file__).resolve().parent
REPO_ROOT = ERP_DIR.parent
for p in (str(ERP_DIR), str(REPO_ROOT)):      # ERP apps (config, core ...) + the shared engines (clock, acting_user)
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
