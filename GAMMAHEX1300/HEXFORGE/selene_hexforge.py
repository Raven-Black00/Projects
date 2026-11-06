#!/usr/bin/env python3
# Selene Hexforge — Bitaxe Gamma Hex 1300 firmware foundry.
# By Alan Klusacek - sonofgrimnir@outlook.com
# Docker + ESP-IDF v6.0.2. Fetches libsecp256k1 (bitcoin-core/secp256k1)
# as the ESP-Miner submodule components/libsecp256k1/libsecp256k1.
# Keep URLs in sync with src/lib/forge/constants.ts.

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import stat
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

REPO_URL = "https://github.com/bitaxeorg/ESP-Miner.git"
SECP_URL = "https://github.com/bitcoin-core/secp256k1.git"
SECP_REL = Path("components/libsecp256k1/libsecp256k1")
SECP_HEADER = SECP_REL / "include" / "secp256k1.h"
IMAGE = "selene-espminer-idf:v6.0.2"
IDF_BASE = "espressif/idf:v6.0.2"

DEFAULT_PROFILE = json.loads(base64.b64decode("eyJob3N0bmFtZSI6ICJnYW1tYWhleCIsICJ3aWZpc3NpZCI6ICIiLCAid2lmaXBhc3MiOiAiIiwgInN0cmF0dW11cmwiOiAicHVibGljLXBvb2wuaW8iLCAic3RyYXR1bXBvcnQiOiAzMzMzLCAic3RyYXR1bXRscyI6IGZhbHNlLCAic3RyYXR1bXVzZXIiOiAiIiwgInN0cmF0dW1wYXNzIjogIngiLCAic3RyYXR1bWRpZmYiOiAxMDAwLCAic3RyYXR1bXhuc3ViIjogZmFsc2UsICJmYnN0cmF0dW11cmwiOiAic29sby5ja3Bvb2wub3JnIiwgImZic3RyYXR1bXBvcnQiOiAzMzMzLCAiZmJzdHJhdHVtdGxzIjogZmFsc2UsICJmYnN0cmF0dW11c2VyIjogIiIsICJmYnN0cmF0dW1wYXNzIjogIngiLCAiZmJzdHJhdHVtZGlmZiI6IDEwMDAsICJhc2ljZnJlcXVlbmN5IjogNjkwLCAiYXNpY3ZvbHRhZ2UiOiAxMjAwLCAiYXV0b2ZhbnNwZWVkIjogdHJ1ZSwgImZhbnNwZWVkIjogMTAwLCAic2VsZnRlc3QiOiB0cnVlLCAib3ZlcmhlYXRNb2RlIjogZmFsc2UsICJyb3RhdGlvbiI6IDB9").decode("utf-8"))

BG = "#100e0c"
SURFACE = "#1b1814"
INK = "#f6f1e7"
MUTED = "#a89b8c"
COPPER = "#e08a3c"
INK_ON_COPPER = "#2a1608"
LINE = "#3c342b"


def _make_writable(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    if os.name == "nt":
        import ctypes
        # FILE_ATTRIBUTE_NORMAL. Git pack files are read-only; chmod alone can miss them.
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x80)


def _remove_path(path: Path) -> None:
    """Delete a file or tree. Git pack files on Windows are read-only (WinError 5)."""

    def retry(func, failed: str) -> None:
        _make_writable(failed)
        func(failed)

    def onexc(func, failed: str, exc: BaseException) -> None:
        retry(func, failed)

    def onerror(func, failed: str, exc_info) -> None:
        retry(func, failed)

    last: Exception | None = None
    for _ in range(5):
        try:
            if path.is_dir() and not path.is_symlink():
                if "onexc" in shutil.rmtree.__code__.co_varnames:
                    shutil.rmtree(path, onexc=onexc)
                else:
                    shutil.rmtree(path, onerror=onerror)
            else:
                _make_writable(path)
                path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last = exc
            time.sleep(0.25)
    message = f"Could not delete {path}. Close Git, Explorer, or any editor using ESP-Miner, then try again."
    raise RuntimeError(message) from last


def csv_text(profile: dict) -> str:
    def bit(value: bool) -> str:
        return "1" if value else "0"

    rows = [
        ("key", "type", "encoding", "value"),
        ("main", "namespace", "", ""),
        ("hostname", "data", "string", profile["hostname"]),
        ("wifissid", "data", "string", profile["wifissid"]),
        ("wifipass", "data", "string", profile["wifipass"]),
        ("stratumurl", "data", "string", profile["stratumurl"]),
        ("stratumport", "data", "u16", str(int(profile["stratumport"]))),
        ("stratumtls", "data", "u16", bit(bool(profile["stratumtls"]))),
        ("stratumcert", "data", "string", "x"),
        ("stratumuser", "data", "string", profile["stratumuser"]),
        ("stratumpass", "data", "string", profile["stratumpass"]),
        ("stratumdiff", "data", "u16", str(int(profile["stratumdiff"]))),
        ("stratumxnsub", "data", "u16", bit(bool(profile["stratumxnsub"]))),
        ("fbstratumurl", "data", "string", profile["fbstratumurl"]),
        ("fbstratumport", "data", "u16", str(int(profile["fbstratumport"]))),
        ("fbstratumtls", "data", "u16", bit(bool(profile["fbstratumtls"]))),
        ("fbstratumcert", "data", "string", "x"),
        ("fbstratumuser", "data", "string", profile["fbstratumuser"]),
        ("fbstratumpass", "data", "string", profile["fbstratumpass"]),
        ("fbstratumdiff", "data", "u16", str(int(profile["fbstratumdiff"]))),
        ("fbstratumxnsum", "data", "u16", "0"),
        ("asicfrequency", "data", "u16", str(int(profile["asicfrequency"]))),
        ("asicvoltage", "data", "u16", str(int(profile["asicvoltage"]))),
        ("asicmodel", "data", "string", "BM1370"),
        ("devicemodel", "data", "string", "GammaHex"),
        ("boardversion", "data", "string", "1300"),
        ("rotation", "data", "u16", str(int(profile["rotation"]))),
        ("autofanspeed", "data", "u16", bit(bool(profile["autofanspeed"]))),
        ("fanspeed", "data", "u16", str(int(profile["fanspeed"]))),
        ("selftest", "data", "u16", bit(bool(profile["selftest"]))),
        ("overheat_mode", "data", "u16", bit(bool(profile["overheatMode"]))),
    ]

    return "".join(",".join(row) + "\n" for row in rows)


class Foundry(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Selene — Gamma Hex 1300")
        self.geometry("1040x780")
        self.minsize(860, 680)
        self.configure(bg=BG)
        self.profile = dict(DEFAULT_PROFILE)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.proc: subprocess.Popen[str] | None = None
        self.stop_flag = False
        self.workdir = tk.StringVar(value=str(Path.home() / "selene-hex1300"))
        self.firmware_dir = tk.StringVar(value="")
        self.progress = tk.DoubleVar(value=0)
        self.percent_label = tk.StringVar(value="0%")
        self.status = tk.StringVar(value="Idle. Docker and ESP-IDF run on this computer, not in a browser.")
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
        style.configure("Card.TLabel", background=SURFACE, foreground=INK)
        style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED)
        style.configure("TEntry", fieldbackground="#242019", foreground=INK, insertcolor=INK)
        style.configure("TButton", background=COPPER, foreground=INK_ON_COPPER, padding=(10, 8), borderwidth=0)
        style.map("TButton", background=[("disabled", "#5c4a38"), ("active", "#f0a25e")])
        style.configure("Line.TButton", background=SURFACE, foreground=INK, padding=(10, 8))
        style.map("Line.TButton", background=[("disabled", "#2a261f"), ("active", "#3c342b")])
        style.configure("TCheckbutton", background=SURFACE, foreground=INK)
        style.configure(
            "Copper.Horizontal.TProgressbar",
            troughcolor=LINE,
            background=COPPER,
            bordercolor=LINE,
            lightcolor=COPPER,
            darkcolor=COPPER,
        )

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="SELENE", font=("TkDefaultFont", 18, "bold")).pack(side="left")
        ttk.Label(
            head,
            text=" Gamma Hex 1300 · 6× BM1370 · ESP-IDF v6.0.2 · libsecp256k1",
            style="Muted.TLabel",
        ).pack(side="left", pady=6)

        credit_row = ttk.Frame(outer)
        credit_row.pack(fill="x")
        ttk.Label(
            credit_row,
            text="By Alan Klusacek - sonofgrimnir@outlook.com",
            style="Muted.TLabel",
            font=("TkDefaultFont", 9),
        ).pack(side="left", pady=(0, 6))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=5)
        body.rowconfigure(0, weight=1)

        form = ttk.Frame(body, style="Card.TFrame", padding=12)
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._form(form)

        side = ttk.Frame(body)
        side.grid(row=0, column=1, sticky="nsew")
        self._actions(side)
        self.log = tk.Text(
            side,
            bg="#14120f",
            fg=INK,
            insertbackground=INK,
            relief="flat",
            wrap="word",
            font=("TkFixedFont", 10),
            height=18,
            padx=10,
            pady=10,
        )
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        self.log.configure(state="disabled")
        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        bar_row = ttk.Frame(outer)
        bar_row.pack(fill="x", pady=(6, 0))
        self.bar = ttk.Progressbar(
            bar_row,
            style="Copper.Horizontal.TProgressbar",
            maximum=100,
            variable=self.progress,
        )
        self.bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(bar_row, textvariable=self.percent_label, width=5).pack(side="right")

    def _field(self, parent: ttk.Frame, label: str, key: str, row: int) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=str(self.profile.get(key, "")))
        entry = ttk.Entry(parent, textvariable=var, show="*" if "pass" in key else "")
        entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))
        self._vars[key] = var

    def _form(self, parent: ttk.Frame) -> None:
        self._vars: dict[str, tk.Variable] = {}
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Board profile", style="Card.TLabel", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        fields = [
            ("Hostname", "hostname"),
            ("Wi-Fi SSID", "wifissid"),
            ("Wi-Fi password", "wifipass"),
            ("Pool host", "stratumurl"),
            ("Pool port", "stratumport"),
            ("Stratum user", "stratumuser"),
            ("Stratum password", "stratumpass"),
            ("Difficulty", "stratumdiff"),
            ("Fallback host", "fbstratumurl"),
            ("Fallback port", "fbstratumport"),
            ("Fallback user", "fbstratumuser"),
            ("Frequency MHz", "asicfrequency"),
            ("Voltage mV", "asicvoltage"),
            ("Fan %", "fanspeed"),
        ]

        for index, (label, key) in enumerate(fields, start=1):
            self._field(parent, label, key, index)
        flags = ttk.Frame(parent, style="Card.TFrame")
        flags.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for key, label in (
            ("stratumtls", "Pool TLS"),
            ("stratumxnsub", "xnsub"),
            ("autofanspeed", "Auto fan"),
            ("selftest", "Self-test"),
        ):
            var = tk.BooleanVar(value=bool(self.profile.get(key, False)))
            self._vars[key] = var
            ttk.Checkbutton(flags, text=label, variable=var).pack(side="left", padx=(0, 10))
        ttk.Label(
            parent,
            text="Stock factory profile is 690 MHz / 1200 mV. Higher settings can overheat six BM1370s.",
            style="CardMuted.TLabel",
            wraplength=320,
        ).grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _actions(self, parent: ttk.Frame) -> None:
        work_row = ttk.Frame(parent)
        work_row.pack(fill="x")
        ttk.Label(work_row, text="Workdir", width=12).pack(side="left")
        ttk.Entry(work_row, textvariable=self.workdir).pack(side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Button(work_row, text="Folder", style="Line.TButton", command=self._pick_dir).pack(side="left", padx=(8, 0))

        firmware_row = ttk.Frame(parent)
        firmware_row.pack(fill="x", pady=(8, 0))
        ttk.Label(firmware_row, text="New firmware", width=12).pack(side="left")
        ttk.Entry(firmware_row, textvariable=self.firmware_dir).pack(side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Button(firmware_row, text="Folder", style="Line.TButton", command=self._pick_firmware).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(firmware_row, text="Replace ESP-Miner", command=self.copy_firmware).pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(8, 0))
        specs = [
            ("Check tools", self.check_tools, "Line.TButton"),
            ("1 Clone + libsecp256k1", self.clone_repo, "TButton"),
            ("2 Build IDF image", self.build_image, "TButton"),
            ("3 Compile firmware", self.compile_firmware, "TButton"),
            ("4 Merge factory bin", self.merge_bin, "TButton"),
            ("Write Hex 1300 config", self.write_config, "Line.TButton"),
            ("Run all", self.run_all, "TButton"),
            ("Stop", self.request_stop, "Line.TButton"),
        ]

        for index, (label, command, style) in enumerate(specs):
            ttk.Button(buttons, text=label, style=style, command=command).grid(
                row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4
            )
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

    def _pick_dir(self) -> None:
        chosen = filedialog.askdirectory(title="Hexforge working directory", initialdir=self.workdir.get() or str(Path.home()))
        if chosen:
            self.workdir.set(chosen)

    def _pick_firmware(self) -> None:
        start = self.firmware_dir.get() or str(Path.home())
        chosen = filedialog.askdirectory(title="Folder with the new firmware", initialdir=start)
        if chosen:
            self.firmware_dir.set(chosen)
            self.copy_firmware()

    def repo(self) -> Path:
        return Path(self.workdir.get()).expanduser() / "ESP-Miner"

    def copy_firmware(self) -> None:
        raw_source = self.firmware_dir.get().strip()
        if not raw_source:
            messagebox.showerror("Selene", "Select the folder that holds the new firmware.")
            return
        if not self.workdir.get().strip():
            messagebox.showerror("Selene", "Choose a Hexforge working directory first.")
            return
        source = Path(raw_source).expanduser()
        dest = self.repo()
        if not source.is_dir():
            messagebox.showerror("Selene", "That firmware folder does not exist.")
            return

        def work() -> None:
            self._replace_esp_miner(source, dest)

        self.start(work, "Replacing ESP-Miner with the selected firmware…")

    def _replace_esp_miner(self, source: Path, dest: Path) -> None:
        source = source.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest.resolve()
        if source == dest or dest.is_relative_to(source) or source.is_relative_to(dest):
            raise RuntimeError("The firmware folder cannot be ESP-Miner, inside it, or contain it.")
        children = list(dest.iterdir())
        self.log_line(f"clearing {dest}")
        for index, child in enumerate(children, start=1):
            if self.stop_flag:
                raise RuntimeError("stopped")
            _remove_path(child)
            self.log_line(f"removed {child.name}")
            if children:
                self.set_progress(20 * index / len(children))
        files = [path for path in source.rglob("*") if path.is_file()]
        if not files:
            raise RuntimeError("That folder has no files to copy.")
        total = len(files)
        for index, src in enumerate(files, start=1):
            if self.stop_flag:
                raise RuntimeError("stopped")
            rel = src.relative_to(source)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            if index == 1 or index == total or index % 25 == 0:
                self.log_line(f"copied {rel.as_posix()}")
            self.set_progress(20 + 80 * index / total)
        self.log_line(f"replaced ESP-Miner with {total} files from {source}")
        self.set_status("ESP-Miner now contains only the selected firmware.")

    def read_profile(self) -> dict:
        profile = dict(self.profile)
        for key, var in self._vars.items():
            value = var.get()
            if key in {"stratumtls", "stratumxnsub", "autofanspeed", "selftest", "overheatMode", "fbstratumtls"}:
                profile[key] = bool(value)
            elif key in {
                "stratumport",
                "fbstratumport",
                "stratumdiff",
                "fbstratumdiff",
                "asicfrequency",
                "asicvoltage",
                "fanspeed",
                "rotation",
            }:
                profile[key] = int(value)
            else:
                profile[key] = str(value)
        for key in ("fbstratumtls", "overheatMode", "fbstratumuser", "fbstratumpass", "rotation"):
            profile.setdefault(key, DEFAULT_PROFILE.get(key))
        self.profile = profile
        return profile

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def set_progress(self, percent: float) -> None:
        self.events.put(("progress", percent))

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
                elif kind == "progress":
                    value = max(0.0, min(100.0, float(payload)))  # type: ignore[arg-type]
                    self.progress.set(value)
                    self.percent_label.set(f"{int(round(value))}%")
                elif kind == "done":
                    self.worker = None
        except queue.Empty:
            pass
        self.after(120, self._drain)

    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start(self, target, status: str) -> None:
        if self.busy():
            messagebox.showinfo("Selene", "A step is already running.")
            return
        self.stop_flag = False
        self.set_progress(0)
        self.set_status(status)
        self.worker = threading.Thread(target=self._guard, args=(target,), daemon=True)
        self.worker.start()

    def _guard(self, target) -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001 — surface toolchain failures in the log
            self.log_line(f"error: {exc}")
            self.set_status("Stopped on an error. See the log.")
        finally:
            self.events.put(("done", ""))

    def request_stop(self) -> None:
        self.stop_flag = True
        proc = self.proc
        if proc and proc.poll() is None:
            proc.terminate()
        self.set_status("Stop requested.")

    def run_cmd(self, args: list[str], cwd: Path | None = None, timeout: int | None = None) -> int:
        if self.stop_flag:
            raise RuntimeError("stopped")
        shown = " ".join(args)
        self.log_line("$ " + shown)
        proc = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            self.log_line(line.rstrip())
            if self.stop_flag:
                proc.terminate()
                break
        code = proc.wait(timeout=timeout)
        self.proc = None
        if code != 0:
            raise RuntimeError(f"command failed ({code}): {shown}")
        return code

    def require_tool(self, name: str) -> None:
        if shutil.which(name) is None:
            raise RuntimeError(f"{name} is not on PATH. Install it, then run this step again.")

    def check_tools(self) -> None:
        def work() -> None:
            self.log_line("— host prerequisites —")
            for name in ("git", "python3", "docker"):
                path = shutil.which(name)
                self.log_line(f"{name}: {path or 'MISSING'}")
            if shutil.which("docker") is None:
                self.set_status("Docker is missing. Install Docker Desktop or Engine, then check again.")
                return
            self.run_cmd(["docker", "info", "--format", "daemon {{.ServerVersion}}"])
            self.log_line(f"ESP-IDF image base: {IDF_BASE}")
            self.log_line(f"libsecp256k1: {SECP_URL}")
            self.log_line(f"submodule path: {SECP_REL.as_posix()}")
            self.set_progress(100)
            self.set_status("Host tools look usable. Next: clone ESP-Miner.")

        self.start(work, "Checking git, Python, and Docker…")

    def ensure_secp(self, root: Path) -> None:
        header = root / SECP_HEADER
        if header.is_file():
            self.log_line(f"libsecp256k1 header present: {header}")
            return
        if (root / ".git").is_dir():
            self.log_line("libsecp256k1 missing — initializing submodules")
            self.run_cmd(["git", "submodule", "update", "--init", "--recursive"], cwd=root)
            if header.is_file():
                self.log_line(f"libsecp256k1 header present: {header}")
                return
        else:
            self.log_line("no git metadata in this tree — fetching libsecp256k1 directly")
        dest = root / SECP_REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            _remove_path(dest)
        self.log_line("submodule still empty — cloning bitcoin-core/secp256k1 directly")
        self.run_cmd(["git", "clone", "--depth", "1", SECP_URL, str(dest)])
        if not header.is_file():
            raise RuntimeError(f"secp256k1.h was not found at {header}")
        self.log_line(f"libsecp256k1 header present: {header}")

    def ensure_source(self) -> Path:
        root = self.repo()
        root.parent.mkdir(parents=True, exist_ok=True)
        if (root / "CMakeLists.txt").is_file() or (root / ".git").is_dir():
            self.log_line(f"using existing ESP-Miner at {root}")
        elif root.exists() and any(root.iterdir()):
            raise RuntimeError(f"{root} is not empty and is not an ESP-Miner tree.")
        else:
            self.run_cmd(["git", "clone", "--recursive", REPO_URL, str(root)])
        if (root / ".git").is_dir():
            self.run_cmd(["git", "config", "--global", "--add", "safe.directory", str(root.resolve())])
        return root

    def clone_repo(self) -> None:
        def work() -> None:
            self.require_tool("git")
            root = self.ensure_source()
            self.ensure_secp(root)
            self.set_progress(100)
            self.set_status("Source and libsecp256k1 are ready.")

        self.start(work, "Preparing ESP-Miner and libsecp256k1…")

    def build_image(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            root = self.repo()
            dockerfile = root / ".devcontainer" / "Dockerfile"
            if not dockerfile.is_file():
                raise RuntimeError("Clone ESP-Miner first — .devcontainer/Dockerfile is missing.")
            self.log_line(f"building {IMAGE} FROM {IDF_BASE}")
            self.run_cmd(["docker", "build", "-t", IMAGE, str(root / ".devcontainer")])
            self.set_progress(100)
            self.set_status("IDF image is built. Next: compile.")

        self.start(work, "Building the ESP-IDF Docker image. The first pull is large.")

    def write_config(self) -> None:
        try:
            profile = self.read_profile()
        except ValueError as exc:
            messagebox.showerror("Selene", f"Profile has a bad number: {exc}")
            return
        root = self.repo()
        if not root.is_dir():
            messagebox.showerror("Selene", "Clone ESP-Miner before writing the config into it.")
            return
        path = root / "configs" / "config-1300.selene.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(csv_text(profile), encoding="utf-8")
        self.log_line(f"wrote {path}")
        self.log_line("boardversion=1300 devicemodel=GammaHex asicmodel=BM1370")
        self.set_status("Hex 1300 NVS config written. Flash it with bitaxetool after the merge.")

    def docker_idf(self, script: str) -> None:
        root = self.repo()
        if not (root / "CMakeLists.txt").is_file():
            raise RuntimeError("Clone ESP-Miner first.")
        self.ensure_secp(root)
        self.run_cmd(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{root.resolve()}:/workspace",
                "-w",
                "/workspace",
                IMAGE,
                "bash",
                "-lc",
                script,
            ]
        )

    def compile_firmware(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            self.log_line("idf.py build · target esp32s3 · AxeOS is bundled by the IDF build")
            self.docker_idf(
                "git config --global --add safe.directory /workspace && idf.py build"
            )
            self.set_progress(100)
            self.set_status("Firmware compiled. Next: merge a factory image.")

        self.start(work, "Compiling inside ESP-IDF. This takes several minutes.")

    def merge_bin(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            self.docker_idf("chmod +x ./merge_bin.sh && ./merge_bin.sh ./esp-miner-merged.bin")
            merged = self.repo() / "esp-miner-merged.bin"
            self.log_line(f"factory image: {merged}")
            self.log_line("flash when the board is on USB:")
            self.log_line(
                "bitaxetool --config ./configs/config-1300.selene.csv --firmware ./esp-miner-merged.bin"
            )
            self.set_progress(100)
            self.set_status("Factory image is ready. Selene does not flash unless you run bitaxetool.")

        self.start(work, "Merging bootloader, partition table, and app…")

    def run_all(self) -> None:
        try:
            profile = self.read_profile()
        except ValueError as exc:
            messagebox.showerror("Selene", f"Profile has a bad number: {exc}")
            return

        def work() -> None:
            self.require_tool("git")
            self.require_tool("docker")
            self.set_progress(5)
            self.run_cmd(["docker", "info", "--format", "daemon {{.ServerVersion}}"])
            root = self.ensure_source()
            self.set_progress(25)
            self.ensure_secp(root)
            self.set_progress(35)
            if not (root / ".devcontainer" / "Dockerfile").is_file():
                raise RuntimeError(".devcontainer/Dockerfile is missing from the checkout.")
            self.run_cmd(["docker", "build", "-t", IMAGE, str(root / ".devcontainer")])
            self.set_progress(60)
            path = root / "configs" / "config-1300.selene.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(csv_text(profile), encoding="utf-8")
            self.log_line(f"wrote {path}")
            self.set_progress(70)
            self.docker_idf("git config --global --add safe.directory /workspace && idf.py build")
            self.set_progress(90)
            self.docker_idf("chmod +x ./merge_bin.sh && ./merge_bin.sh ./esp-miner-merged.bin")
            self.log_line("done. install the flasher with: pip install --upgrade bitaxetool")
            self.set_progress(100)
            self.set_status("Build finished. Factory bin is esp-miner-merged.bin next to the repo.")

        self.start(work, "Running the full Hex 1300 toolchain…")


if __name__ == "__main__":
    Foundry().mainloop()
