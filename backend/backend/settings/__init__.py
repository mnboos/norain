"""Load the project environment before any settings module is evaluated."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _dotenv_path() -> Path:
    configured_path = os.environ.get("ENV_FILE")
    if not configured_path:
        return PROJECT_ROOT / ".env"

    path = Path(configured_path).expanduser()
    # Commands normally run from backend/, while ENV_FILE is configured relative
    # to the repository root. Resolve it consistently regardless of the cwd.
    return path if path.is_absolute() else PROJECT_ROOT / path


DOTENV_PATH = _dotenv_path()
if DOTENV_PATH.is_file():
    load_dotenv(DOTENV_PATH)
elif os.environ.get("ENV_FILE"):
    raise RuntimeError(f"ENV_FILE does not point to a file: {DOTENV_PATH}")
