"""Put the anki-notes skill directory on sys.path so its module is importable."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / ".claude" / "skills" / "anki-notes"

for path in (REPO_ROOT, SKILL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
