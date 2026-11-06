@echo off
setlocal
cd /d "%~dp0"
title Hex Radar - Build EXE

set SCRIPT=hex_radar.py
set APPNAME=HexRadar
set VENV=.buildenv

if not exist "%SCRIPT%" (
    echo ERROR: %SCRIPT% not found in %cd%
    pause
    exit /b 1
)

REM ---- pick a standard (non free-threaded) Python, newest stable first
set PYSEL=
for %%V in (3.13 3.12 3.14 3.11) do (
    if not defined PYSEL (
        py -%%V -c "import sys" >nul 2>nul && set PYSEL=py -%%V
    )
)
if not defined PYSEL (
    echo WARNING: no Python 3.11-3.14 found via the py launcher. Falling back to default python.
    echo          If this fails, install Python 3.13 from python.org and run again.
    set PYSEL=python
)
echo Using: %PYSEL%

echo [1/5] Creating clean build environment (%VENV%)...
if exist "%VENV%" rmdir /s /q "%VENV%"
%PYSEL% -m venv "%VENV%"
if errorlevel 1 goto :fail
set VPY=%VENV%\Scripts\python.exe

echo [2/5] Installing build dependencies (no numpy needed)...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install pyinstaller pystray pillow
if errorlevel 1 goto :fail

echo [3/5] Cleaning old build...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APPNAME%.spec" del /q "%APPNAME%.spec"

echo [4/5] Building %APPNAME%.exe ...
"%VPY%" -m PyInstaller --noconfirm --onefile --windowed ^
    --name "%APPNAME%" ^
    --hidden-import pystray._win32 ^
    --exclude-module numpy ^
    "%SCRIPT%"
if errorlevel 1 goto :fail

echo [5/5] Done.
if exist "dist\%APPNAME%.exe" (
    echo.
    echo SUCCESS: %cd%\dist\%APPNAME%.exe
    explorer "dist"
) else (
    goto :fail
)
pause
exit /b 0

:fail
echo.
echo BUILD FAILED - see messages above.
pause
exit /b 1
