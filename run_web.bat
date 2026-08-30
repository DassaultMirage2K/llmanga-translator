@echo off
setlocal
title llmanga-translator web server
cd /d "%~dp0"

rem --- Ensure ALL dependencies are installed (idempotent) -------------------
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] 'uv' was not found on PATH.
    echo Install it from https://docs.astral.sh/uv/getting-started/installation/ and re-run.
    pause
    exit /b 1
)

echo Ensuring dependencies are installed (uv sync)...
uv sync
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See the messages above.
    pause
    exit /b 1
)

rem --- Start the server ------------------------------------------------------
echo Starting llmanga-translator web server...
echo Open http://127.0.0.1:8000 in your browser (Ctrl+C to stop).
echo.
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

echo.
pause
endlocal
