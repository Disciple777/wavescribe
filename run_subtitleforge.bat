@echo off
title SubtitleForge
echo ============================================
echo   SubtitleForge - AI Subtitle Generator
echo ============================================
echo.
echo Launching SubtitleForge...
echo.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found!
    echo.
    echo Please create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe subtitleforge\main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] SubtitleForge exited with code %ERRORLEVEL%
    echo Check the error message above.
    echo.
    pause
)
