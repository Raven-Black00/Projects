@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "%~dp0selene_forge.py"
  exit /b %errorlevel%
)
python "%~dp0selene_forge.py"
