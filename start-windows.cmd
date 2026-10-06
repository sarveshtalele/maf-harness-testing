@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (echo Install uv first: https://docs.astral.sh/uv/getting-started/installation/ & exit /b 1)
where npm >nul 2>nul
if errorlevel 1 (echo Install Node.js 24 LTS first: https://nodejs.org/ & exit /b 1)
for /f "tokens=*" %%i in ('node --version 2^>nul') do set NODE_VERSION=%%i
if not defined NODE_VERSION (echo Node.js not found. Install Node.js 24 LTS: https://nodejs.org/ & exit /b 1)
echo %NODE_VERSION% | findstr /R "^v24\." >nul 2>nul
if errorlevel 1 (
    echo Node.js 24 is required. Found %NODE_VERSION%.
    echo Installed Node.js versions:
    where node
    echo.
    echo Fix: Install Node.js 24 LTS ^(winget install OpenJS.NodeJS.LTS^) or with nvm-windows run: nvm install 24 ^&^& nvm use 24; then remove older Node entries from PATH and open a new terminal.
    exit /b 1
)
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
