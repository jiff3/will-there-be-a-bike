"""Repository entry point; implementation is tested in the API package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.render_release import main

if __name__ == "__main__":
    main()
