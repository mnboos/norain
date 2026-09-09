#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""

import os
import sys

from pathlib import Path

from dotenv import load_dotenv


def main():
    if dotenv_path := os.environ.get("ENV_FILE"):
        dotenv_path = Path(dotenv_path)
    else:
        dotenv_path = Path(__file__).parent.parent / ".env"
    assert dotenv_path.is_file(), f"No .env file was found at: {dotenv_path}"
    print("Loading dotenv: ", dotenv_path)
    load_dotenv(dotenv_path, verbose=True)

    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
