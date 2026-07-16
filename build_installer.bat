@echo off
REM ============================================================
REM  WaveScribe Installer Builder
REM  Run this to build the Windows installer (requires Inno Setup)
REM ============================================================
title WaveScribe Installer Builder

echo.
echo ============================================================
echo  WaveScribe Installer Builder
echo ============================================================
echo.

REM Check if Inno Setup is installed
set ISCC_PATH=
if exist "C:\Program Files (x86)\Inno Setup 6\iscc.exe" set ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\iscc.exe
if exist "C:\Program Files\Inno Setup 6\iscc.exe" set ISCC_PATH=C:\Program Files\Inno Setup 6\iscc.exe

if "%ISCC_PATH%"=="" (
    echo [WARN] Inno Setup not found. Install it from:
    echo        https://jrsoftware.org/isdl.php
    echo.
    echo You can distribute the dist/WaveScribe folder directly
    echo or the installer/WaveScribe-Portable.zip archive.
    echo.
    pause
    exit /b 1
)

echo [OK] Found Inno Setup at: %ISCC_PATH%
echo.

REM Build the installer
echo [BUILD] Running Inno Setup compiler...
"%ISCC_PATH%" installer.iss

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo  [SUCCESS] Installer created in the installer/ folder!
    echo ============================================================
) else (
    echo.
    echo [FAIL] Installer build failed with error code %ERRORLEVEL%
)

pause
