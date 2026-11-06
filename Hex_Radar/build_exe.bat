@echo off
setlocal enabledelayedexpansion
title Hex Radar - Build EXE
cd /d "%~dp0"

set APP=HexRadar
set SRC=hex_radar.py

if not exist "%SRC%" (
    echo [ERROR] %SRC% not found next to this script.
    pause & exit /b 1
)

where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
    echo [ERROR] Python not found. Install Python 3.9+ and tick "Add to PATH".
    pause & exit /b 1
)

echo [1/4] Creating build venv...
if not exist ".buildvenv\Scripts\python.exe" (
    %PY% -m venv .buildvenv || (echo [ERROR] venv failed & pause & exit /b 1)
)
set VPY=.buildvenv\Scripts\python.exe

echo [2/4] Installing PyInstaller...
"%VPY%" -m pip install --upgrade pip pyinstaller >nul || (echo [ERROR] pip failed & pause & exit /b 1)

echo [3/4] Cleaning old build...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q "%APP%.spec" 2>nul

echo [4/4] Building %APP%.exe...
set ICON=
if exist "hex_radar.ico" set ICON=--icon "hex_radar.ico"

"%VPY%" -m PyInstaller --noconfirm --onefile --windowed --clean ^
    --name "%APP%" %ICON% ^
    --exclude-module numpy --exclude-module matplotlib --exclude-module PIL ^
    "%SRC%"

if errorlevel 1 (
    echo [ERROR] Build failed.
    pause & exit /b 1
)

rmdir /s /q build 2>nul
del /q "%APP%.spec" 2>nul

echo.
echo ===== DONE =====
echo Output: %cd%\dist\%APP%.exe
echo Settings are stored in %%USERPROFILE%%\.hex_radar.json
echo Tip: run "dist\%APP%.exe --demo" from a terminal to test without a miner.
pause
