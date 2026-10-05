@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/ & exit /b 1)
where npm >nul 2>nul
if errorlevel 1 (echo Install Node.js 24 LTS first: https://nodejs.org/ & exit /b 1)
if not exist .env (
    copy .env.example .env >nul
    if errorlevel 1 exit /b 1
    echo Created .env. Configure your gateway there before starting a run.
)
uv run --no-project --python 3.12 python scripts/start_ui.py --check-only %*
if "%errorlevel%"=="10" exit /b 0
if errorlevel 1 exit /b %errorlevel%
uv sync --python 3.12 --extra dev --extra e2e
if errorlevel 1 exit /b %errorlevel%
uv run python scripts/start_ui.py --prepare %*
if errorlevel 1 exit /b %errorlevel%
uv run python scripts/start_ui.py --production %*
exit /b %errorlevel%
