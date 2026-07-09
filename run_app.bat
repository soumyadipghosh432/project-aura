@echo off
title Project Aura - Offline Support Workstation
echo ==========================================================
echo Starting Project Aura Web Application...
echo Operating System: Windows
echo Shell: CMD / PowerShell
echo Environment: 100%% Offline / Air-Gapped Laptop Deployment
echo ==========================================================
echo.

:: Activate Python Virtual Environment
if not exist ".\venv\Scripts\activate.bat" (
    echo [CRITICAL ERROR] Python virtual environment was not detected.
    echo Please ensure the "venv" folder exists in the project root.
    echo run MANUAL_INSTALL.md instructions to build.
    pause
    exit /b 1
)

echo Activating virtual environment...
call .\venv\Scripts\activate.bat

echo launching local web server on port 8000...
echo Please access http://localhost:8000 in your browser.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

pause
