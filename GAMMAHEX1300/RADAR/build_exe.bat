@echo off
setlocal
cd /d "%~dp0"
echo === HexRadar EXE builder ===
where python >nul 2>nul || (echo Python not found in PATH & pause & exit /b 1)
python -m pip install --upgrade pip pyinstaller pystray pillow || goto :err
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
python -m PyInstaller --noconfirm --clean --onefile --windowed --name HexRadar ^
  --hidden-import pystray._win32 --hidden-import PIL._tkinter_finder ^
  hex_radar.py || goto :err
echo.
echo Done: %~dp0dist\HexRadar.exe
pause
exit /b 0
:err
echo BUILD FAILED
pause
exit /b 1
