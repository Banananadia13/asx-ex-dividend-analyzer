@echo off
rem ASX Ex-Dividend Recovery Pattern Analyzer — Windows launcher
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3 is required - install from python.org & pause & exit /b 1)
if not exist .venv (
  echo First run - creating virtual environment and installing dependencies...
  python -m venv .venv
  .venv\Scripts\pip install --quiet --upgrade pip
  .venv\Scripts\pip install --quiet -r requirements.txt
)
echo Starting... open http://127.0.0.1:8477 in your browser (Ctrl+C to stop).
start "" http://127.0.0.1:8477
cd backend
..\.venv\Scripts\uvicorn main:app --host 127.0.0.1 --port 8477
