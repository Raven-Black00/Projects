from pathlib import Path
import ast
import os
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parent
APP = ROOT / "selene_hexforge-2.1.1.py"
NAME = "SeleneHexforge-2.1.1"

def run(args, log):
    args = [str(a) for a in args]
    line = "\n> " + subprocess.list2cmdline(args)
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()
    proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        print(line, end="", flush=True)
        log.write(line)
        log.flush()
    code = proc.wait()
    if code:
        raise RuntimeError(f"Command failed with exit code {code}. See build-exe.log for the underlying error.")

def main():
    if sys.platform != "win32":
        raise RuntimeError("Run this builder on Windows, not WSL/Linux.")
    if sys.version_info < (3, 10):
        raise RuntimeError("Windows Python 3.10 or newer is required.")
    ast.parse(APP.read_text(encoding="utf-8-sig"))
    python = ROOT / ".venv-build-minimal" / "Scripts" / "python.exe"
    with (ROOT / "build-exe.log").open("w", encoding="utf-8") as log:
        print("Using Python: " + sys.version)
        log.write("Python: " + sys.version + "\nExecutable: " + sys.executable + "\n")
        if not python.exists():
            run([sys.executable, "-m", "venv", ROOT / ".venv-build-minimal"], log)
        run([python, "-m", "pip", "install", "--upgrade", "pip"], log)
        run([python, "-m", "pip", "install", "--upgrade", "pyinstaller", "tkinterdnd2"], log)
        run([python, "-c", "import tkinter, tkinterdnd2; r=tkinter.Tk(); r.withdraw(); r.destroy()"], log)
        args = [python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
                "--name", NAME, "--collect-all", "tkinterdnd2", "--exclude-module", "py7zr",
                "--distpath", ROOT / "dist", "--workpath", ROOT / "build-minimal", "--specpath", ROOT]
        icon = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "icon.ico"
        if len(sys.argv) > 1 and not icon.is_file():
            raise RuntimeError("Icon does not exist: " + str(icon))
        if icon.is_file():
            args += ["--icon", icon]
        run(args + [APP], log)
    exe = ROOT / "dist" / (NAME + ".exe")
    if not exe.is_file() or exe.stat().st_size == 0:
        raise RuntimeError("No non-empty EXE was produced.")
    print("\nSUCCESS: " + str(exe))
    print("Install Git and Docker separately for firmware builds.")
    print("Install 7-Zip separately for .7z/.rar extraction; py7zr is intentionally excluded.")
    print("bitaxetool is a separate flashing CLI and is not bundled.")
    os.startfile(str(exe.parent))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
