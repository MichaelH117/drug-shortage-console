# One-command demo on Windows (OPS-01). Offline: SYNTHETIC fixtures, no API key, no network.
# Usage (from the repo root):  powershell -ExecutionPolicy Bypass -File .\run_demo.ps1
$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
.\.venv\Scripts\python.exe -m pipeline.run --fixtures
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -m console.simulate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -m streamlit run app/Home.py
