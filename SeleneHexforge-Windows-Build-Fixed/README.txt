Selene Hexforge - corrected minimal Windows EXE builder

This is a build package, not a compiled Windows EXE.

1. Extract the entire ZIP into a writable folder on Windows.
2. Double-click Build-EXE.bat.
3. On success, dist contains SeleneHexforge-2.1.1.exe.

Windows Python 3.10+ with Tkinter and pip is required for building.
The builder creates .venv-build-minimal, separate from the old failed environment.
Only PyInstaller and tkinterdnd2 are explicitly installed for packaging.
py7zr is excluded from the EXE; its compression dependency chain is avoided.
The application source is unchanged, including its optional ImportError fallback.
ZIP/TAR extraction remains in the standard library.
Install external 7-Zip for .7z/.rar extraction. py7zr fallback is unavailable.
Git and a running Docker installation remain separate firmware build requirements.
bitaxetool remains a separate external flashing CLI and is not bundled.

Optional: place icon.ico beside the builder, or pass its path to Build-EXE.bat.
Errors are recorded in build-exe.log. If dependency installation still fails,
provide the pip ERROR lines above the Python traceback or the full build-exe.log.

Syntax and ZIP integrity verified here. Windows compilation has not been tested.
