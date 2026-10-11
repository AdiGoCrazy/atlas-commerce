"""Streamlit entry point for the Atlas Commerce operator workspace."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from adaptive_commerce.presentation.dashboard import render_dashboard

render_dashboard()
