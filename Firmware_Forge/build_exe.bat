@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Selene Forge - build exe

where python >nul 2>nul
if errorlevel 1 (
  echo Python not found in PATH.
  pause
  exit /b 1
)

echo [1/5] Closing any running SeleneForge.exe ...
taskkill /F /IM SeleneForge.exe >nul 2>nul
timeout /t 2 /nobreak >nul

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESK=%%D"
if not defined DESK set "DESK=%USERPROFILE%\Desktop"
set "FINAL=%DESK%\dist"
set "WORK=%~dp0sf_build"
set "LOCALDIST=%~dp0sf_dist"

echo [2/5] Cleaning old build output ...
if exist "%WORK%" rmdir /s /q "%WORK%"
if exist "%LOCALDIST%" rmdir /s /q "%LOCALDIST%"
if exist "%~dp0SeleneForge.spec" del /f /q "%~dp0SeleneForge.spec"
if exist "%FINAL%\SeleneForge.exe" del /f /q "%FINAL%\SeleneForge.exe"
if exist "%FINAL%\SeleneForge.exe" (
  echo WARNING: cannot delete %FINAL%\SeleneForge.exe  - it is locked by a program or antivirus.
  ren "%FINAL%\SeleneForge.exe" "SeleneForge_old_%RANDOM%.exe" 2>nul
)

echo [3/5] Installing/updating PyInstaller ...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto :fail

echo [4/5] Building into a local folder first (avoids Desktop/OneDrive/antivirus locks) ...
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name SeleneForge ^
  --distpath "%LOCALDIST%" --workpath "%WORK%" --specpath "%WORK%" "%~dp0selene_forge_gui.py"
if errorlevel 1 goto :fail
if not exist "%LOCALDIST%\SeleneForge.exe" goto :fail

echo [5/5] Copying to %FINAL% ...
if not exist "%FINAL%" mkdir "%FINAL%"
copy /y "%LOCALDIST%\SeleneForge.exe" "%FINAL%\SeleneForge.exe" >nul
if errorlevel 1 (
  echo.
  echo Could not write to %FINAL%.
  echo Your exe is safe here: %LOCALDIST%\SeleneForge.exe
  echo Add %FINAL% and %LOCALDIST% to Windows Security exclusions, then copy it by hand.
  pause
  exit /b 2
)
echo.
echo Done: %FINAL%\SeleneForge.exe
pause
exit /b 0

:fail
echo.
echo Build failed. Read the lines above. If you see Access is denied:
echo  1. Close SeleneForge.exe if it is running (Task Manager).
echo  2. Add this folder and your Desktop\dist to Windows Security exclusions.
echo  3. Check Windows Security - Protection history for a quarantined SeleneForge.exe.
pause
exit /b 1
