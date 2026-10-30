#!/usr/bin/env sh
# One-command demo on macOS/Linux (OPS-01). Offline: SYNTHETIC fixtures, no API key, no network.
# Usage (from the repo root):  sh run_demo.sh
set -e
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
.venv/bin/python -m pipeline.run --fixtures
.venv/bin/python -m console.simulate
.venv/bin/python -m streamlit run app/Home.py
