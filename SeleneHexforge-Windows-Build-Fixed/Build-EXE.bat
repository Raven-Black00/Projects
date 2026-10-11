@echo off
setlocal
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1
if not errorlevel 1 goto use_python
echo ERROR: Install Windows Python 3.10 or newer with Tkinter and pip.
pause
exit /b 1
:use_py
py -3 build_exe.py %*
goto finished
:use_python
python build_exe.py %*
:finished
if errorlevel 1 goto failed
echo Build complete. Your EXE is in the dist folder.
pause
exit /b 0
:failed
echo Build FAILED. Check build-exe.log for pip ERROR lines above the traceback.
pause
exit /b 1
