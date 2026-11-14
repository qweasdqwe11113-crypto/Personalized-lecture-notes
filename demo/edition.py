"""Where this install keeps its data, and whether it carries the demonstration content.

The development and test setup (run_demo.sh, the test suite) keeps its data in the repository
and ships the demonstration courses, students and built-in lessons. The desktop release sets
ZHIYE_EDITION=release and points ZHIYE_DATA_DIR at the user's own folder: the database starts
empty, no built-in lesson or fixed experiment appears, and .env and .runtime live in that folder,
so the program files can stay read-only.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir():
    """Folder holding .env (model settings) and .runtime (uploads, grading, slide reviews)."""
    return Path(os.environ.get('ZHIYE_DATA_DIR') or ROOT)


def release():
    """True in the desktop release: no demonstration data is seeded or listed."""
    return os.environ.get('ZHIYE_EDITION', '').strip().lower() == 'release'
