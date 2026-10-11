"""Launch Atlas's workflow API process."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from adaptive_commerce.application.orchestration import serve_orchestrator

serve_orchestrator(Path(os.getenv("ATLAS_DB_PATH", "data/atlas_commerce.db")))
