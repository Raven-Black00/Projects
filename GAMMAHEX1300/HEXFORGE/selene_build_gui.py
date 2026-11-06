#!/usr/bin/env python3
# Selene Build Forge -- GUI wrapper around PyInstaller for selene_hexforge.py
# Lets you pick the script, an .ico icon, output name, and onefile/windowed flags,
# then runs PyInstaller and streams the log live.

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

BG = "#100e0c"
SURFACE = "#1b1814"
INK = "#f6f1e7"
MUTED = "#a89b8c"
COPPER = "#e08a3c"
INK_ON_COPPER = "#2a1608"
LINE = "#3c342b"

HERE = Path(__file__).resolve().parent
DEFAULT_SCRIPT = HERE / "selene_hexforge.py"


class BuildForge(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Selene Build Forge")
        self.geometry("760x600")
        self.minsize(680, 540)
        self.configure(bg=BG)

        self.script_path = tk.StringVar(value=str(DEFAULT_SCRIPT if DEFAULT_SCRIPT.exists() else ""))
        self.icon_path = tk.StringVar(value="")
        self.out_name = tk.StringVar(value="Selene-Hexforge")
        self.onefile = tk.BooleanVar(value=True)
        self.windowed = tk.BooleanVar(value=True)
        self.clean_build = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Idle.")

        self.events: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None

        self._style()
        self._build()
        self.after(120, self._drain)

    def _style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, font=("TkDefaultFont", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("TEntry", fieldbackground="#242019", foreground=INK, insertcolor=INK)
        style.configure("TButton", background=COPPER, foreground=INK_ON_COPPER, padding=(10, 8), borderwidth=0)
        style.map("TButton", background=[("disabled", "#5c4a38"), ("active", "#f0a25e")])
        style.configure("Line.TButton", background=SURFACE, foreground=INK, padding=(10, 8))
        style.map("Line.TButton", background=[("disabled", "#2a261f"), ("active", "#3c342b")])
        style.configure("TCheckbutton", background=BG, foreground=INK)
        style.configure(
            "Copper.Horizontal.TProgressbar",
            troughcolor=LINE, background=COPPER, bordercolor=LINE,
            lightcolor=COPPER, darkcolor=COPPER,
        )

    def _row(self, parent, label, var, browse_cmd=None, show_preview=False):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=label, width=14).pack(side="left")
        entry = ttk.Entry(row, textvariable=var)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        if browse_cmd:
            ttk.Button(row, text="Browse", style="Line.TButton", command=browse_cmd).pack(side="left", padx=(8, 0))
        return row

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="SELENE BUILD FORGE", font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
        ttk.Label(outer, text="PyInstaller wrapper -- pick your script, icon, and go.", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 12)
        )

        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.pack(fill="x")

        self._row(card, "Script (.py)", self.script_path, self._pick_script)
        self._row(card, "Icon (.ico)", self.icon_path, self._pick_icon)
        self._row(card, "Output name", self.out_name)

        flags = ttk.Frame(card)
        flags.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(flags, text="Onefile", variable=self.onefile).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(flags, text="Windowed (no console)", variable=self.windowed).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(flags, text="Clean build/dist first", variable=self.clean_build).pack(side="left")

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(12, 0))
        self.build_btn = ttk.Button(actions, text="Build .exe", command=self.on_build)
        self.build_btn.pack(side="left")
        ttk.Button(actions, text="Open dist folder", style="Line.TButton", command=self.open_dist).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(10, 4))
        self.bar = ttk.Progressbar(outer, style="Copper.Horizontal.TProgressbar", mode="indeterminate")
        self.bar.pack(fill="x", pady=(0, 10))

        self.log = tk.Text(
            outer, bg="#14120f", fg=INK, insertbackground=INK, relief="flat",
            wrap="word", font=("TkFixedFont", 9), padx=10, pady=10,
        )
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")

    def _pick_script(self) -> None:
        start = str(Path(self.script_path.get() or HERE))
        chosen = filedialog.askopenfilename(
            title="Choose the Python script to package",
            initialdir=str(Path(start).parent if Path(start).exists() else HERE),
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if chosen:
            self.script_path.set(chosen)

    def _pick_icon(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Choose an icon",
            initialdir=str(HERE),
            filetypes=[("Icon files", "*.ico"), ("All files", "*.*")],
        )
        if chosen:
            self.icon_path.set(chosen)

    def open_dist(self) -> None:
        script = Path(self.script_path.get() or "")
        dist = (script.parent if script.exists() else HERE) / "dist"
        if not dist.exists():
            messagebox.showinfo("Selene", "No dist folder yet. Build first.")
            return
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", str(dist)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(dist)])
        else:
            subprocess.Popen(["xdg-open", str(dist)])

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", str(payload) + "\n")
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "status":
                    self.status.set(str(payload))
                elif kind == "done":
                    self.build_btn.configure(state="normal")
                    self.bar.stop()
                    self.worker = None
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def on_build(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        script = Path(self.script_path.get().strip()).expanduser()
        if not script.is_file():
            messagebox.showerror("Selene", "Pick a valid .py script first.")
            return
        icon = self.icon_path.get().strip()
        if icon and not Path(icon).is_file():
            messagebox.showerror("Selene", "Icon file not found.")
            return
        name = self.out_name.get().strip() or script.stem

        if shutil.which("pyinstaller") is None:
            self.log_line("PyInstaller not found on PATH. Installing via pip...")

        self.build_btn.configure(state="disabled")
        self.bar.start(12)
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.set_status("Building...")

        self.worker = threading.Thread(
            target=self._run_build, args=(script, icon, name), daemon=True
        )
        self.worker.start()

    def _run_build(self, script: Path, icon: str, name: str) -> None:
        workdir = script.parent
        try:
            if shutil.which("pyinstaller") is None:
                self._stream(
                    [sys.executable, "-m", "pip", "install", "pyinstaller"], cwd=workdir
                )

            if self.clean_build.get():
                for folder in ("build", "dist"):
                    target = workdir / folder
                    if target.exists():
                        self.log_line(f"removing {target}")
                        shutil.rmtree(target, ignore_errors=True)
                spec = workdir / f"{name}.spec"
                if spec.exists():
                    spec.unlink()

            cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", name]
            if self.onefile.get():
                cmd.append("--onefile")
            if self.windowed.get():
                cmd.append("--windowed")
            if icon:
                cmd += ["--icon", icon]
            cmd.append(str(script))

            self.log_line("Running: " + " ".join(cmd))
            code = self._stream(cmd, cwd=workdir)

            exe_name = name + (".exe" if sys.platform.startswith("win") else "")
            exe_path = workdir / "dist" / exe_name
            if code == 0 and exe_path.exists():
                self.set_status(f"Done: {exe_path}")
                self.log_line(f"SUCCESS: {exe_path}")
            else:
                self.set_status("Build failed. See log above.")
                self.log_line("BUILD FAILED.")
        except Exception as exc:  # noqa: BLE001
            self.log_line(f"ERROR: {exc}")
            self.set_status("Build failed.")
        finally:
            self.events.put(("done", None))

    def _stream(self, cmd: list[str], cwd: Path) -> int:
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.log_line(line.rstrip())
        self.proc.wait()
        return self.proc.returncode


if __name__ == "__main__":
    BuildForge().mainloop()
