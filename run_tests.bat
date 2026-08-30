@echo off
setlocal
title llmanga-translator test suite
cd /d "%~dp0"

rem --- Ensure uv is available ---------------------------------------------
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] 'uv' was not found on PATH.
    echo Install it from https://docs.astral.sh/uv/getting-started/installation/ and re-run.
    pause
    exit /b 1
)

rem --- Ensure ALL dependencies are installed, incl. the dev group (pytest) --
echo Ensuring dependencies are installed (uv sync)...
uv sync
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. See the messages above.
    pause
    exit /b 1
)

rem --- Run the test suite --------------------------------------------------
echo Running tests...
echo.
uv run python -m pytest tests -v --tb=short
set EXITCODE=%errorlevel%

echo.
if %EXITCODE% neq 0 (
    echo [FAILED] One or more tests failed.
) else (
    echo [OK] All tests passed.
)
pause
exit /b %EXITCODE%
