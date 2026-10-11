"""Launch one isolated Atlas specialist worker selected by environment."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from adaptive_commerce.agents.subagent import serve_agent

serve_agent(os.environ["ATLAS_AGENT_ROLE"], port=int(os.getenv("ATLAS_AGENT_PORT", "8503")))
