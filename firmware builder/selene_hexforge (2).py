#!/usr/bin/env python3
# Selene Hexforge v2 - Bitaxe Gamma Hex 1300 firmware foundry.
# Docker + ESP-IDF. Fetches libsecp256k1 (bitcoin-core/secp256k1) as the
# ESP-Miner submodule components/libsecp256k1/libsecp256k1 (pinned commit).
# Keep URLs in sync with src/lib/forge/constants.ts.

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

REPO_URL = "https://github.com/bitaxeorg/ESP-Miner.git"
SECP_URL = "https://github.com/bitcoin-core/secp256k1.git"
SECP_REL = Path("components/libsecp256k1/libsecp256k1")
SECP_HEADER = SECP_REL / "include" / "secp256k1.h"
IMAGE = "selene-espminer-idf:v6.0.2"
IDF_BASE = "espressif/idf:v6.0.2"
BUILD_NAME = "selene-build"
CCACHE_VOL = "selene-ccache"
NPM_VOL = "selene-npm-cache"
CONFIG_NAME = "config-1300.selene.csv"

STOCK_FREQ = 690
STOCK_VOLT = 1200

DEFAULT_PROFILE = {
    "hostname": "gammahex",
    "wifissid": "",
    "wifipass": "",
    "stratumurl": "public-pool.io",
    "stratumport": 3333,
    "stratumtls": False,
    "stratumuser": "",
    "stratumpass": "x",
    "stratumdiff": 1000,
    "stratumxnsub": False,
    "fbstratumurl": "solo.ckpool.org",
    "fbstratumport": 3333,
    "fbstratumtls": False,
    "fbstratumuser": "",
    "fbstratumpass": "x",
    "fbstratumdiff": 1000,
    "fbstratumxnsub": False,
    "asicfrequency": STOCK_FREQ,
    "asicvoltage": STOCK_VOLT,
    "autofanspeed": True,
    "fanspeed": 100,
    "selftest": True,
    "overheatMode": False,
    "rotation": 0,
}

INT_KEYS = (
    "stratumport", "fbstratumport", "stratumdiff", "fbstratumdiff",
    "asicfrequency", "asicvoltage", "fanspeed", "rotation",
)
BOOL_KEYS = (
    "stratumtls", "stratumxnsub", "fbstratumtls", "fbstratumxnsub",
    "autofanspeed", "selftest", "overheatMode",
)

# (nvs key, nvs type, source) - source is ("p", profile key) or ("c", constant)
NVS_ROWS = [
    ("hostname", "string", ("p", "hostname")),
    ("wifissid", "string", ("p", "wifissid")),
    ("wifipass", "string", ("p", "wifipass")),
    ("stratumurl", "string", ("p", "stratumurl")),
    ("stratumport", "u16", ("p", "stratumport")),
    ("stratumtls", "u16", ("p", "stratumtls")),
    ("stratumcert", "string", ("c", "x")),
    ("stratumuser", "string", ("p", "stratumuser")),
    ("stratumpass", "string", ("p", "stratumpass")),
    ("stratumdiff", "u16", ("p", "stratumdiff")),
    ("stratumxnsub", "u16", ("p", "stratumxnsub")),
    ("fbstratumurl", "string", ("p", "fbstratumurl")),
    ("fbstratumport", "u16", ("p", "fbstratumport")),
    ("fbstratumtls", "u16", ("p", "fbstratumtls")),
    ("fbstratumcert", "string", ("c", "x")),
    ("fbstratumuser", "string", ("p", "fbstratumuser")),
    ("fbstratumpass", "string", ("p", "fbstratumpass")),
    ("fbstratumdiff", "u16", ("p", "fbstratumdiff")),
    ("fbstratumxnsub", "u16", ("p", "fbstratumxnsub")),
    ("asicfrequency", "u16", ("p", "asicfrequency")),
    ("asicvoltage", "u16", ("p", "asicvoltage")),
    ("asicmodel", "string", ("c", "BM1370")),
    ("devicemodel", "string", ("c", "GammaHex")),
    ("boardversion", "string", ("c", "1300")),
    ("rotation", "u16", ("p", "rotation")),
    ("autofanspeed", "u16", ("p", "autofanspeed")),
    ("fanspeed", "u16", ("p", "fanspeed")),
    ("selftest", "u16", ("p", "selftest")),
    ("overheat_mode", "u16", ("p", "overheatMode")),
]

FIELDS = [
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
    ("Fallback password", "fbstratumpass"),
    ("Fallback difficulty", "fbstratumdiff"),
    ("Frequency MHz", "asicfrequency"),
    ("Voltage mV", "asicvoltage"),
    ("Fan %", "fanspeed"),
]
FLAGS = (
    ("stratumtls", "Pool TLS"),
    ("stratumxnsub", "xnsub"),
    ("fbstratumtls", "Fallback TLS"),
    ("autofanspeed", "Auto fan"),
    ("selftest", "Self-test"),
)

BG = "#100e0c"
SURFACE = "#1b1814"
INK = "#f6f1e7"
MUTED = "#a89b8c"
COPPER = "#e08a3c"
INK_ON_COPPER = "#2a1608"
LINE = "#3c342b"

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
IGNORE_DIRS_ANYWHERE = {"node_modules", "__pycache__"}
MAX_LOG_LINES = 5000
MAX_EVENTS_PER_TICK = 300


class StopRequested(RuntimeError):
    """Raised inside the worker when the user presses Stop."""


def _make_writable(path) -> None:
    p = str(path)
    try:
        mode = stat.S_IRWXU if os.path.isdir(p) and not os.path.islink(p) else stat.S_IWRITE | stat.S_IREAD
        os.chmod(p, mode)
    except OSError:
        pass
    if os.name == "nt":
        import ctypes  # FILE_ATTRIBUTE_NORMAL; git pack files are read-only
        ctypes.windll.kernel32.SetFileAttributesW(p, 0x80)


def _remove_path(path: Path) -> None:
    """Delete a file or tree. Handles read-only git pack files on Windows."""

    def retry(func, failed) -> None:
        _make_writable(failed)
        func(failed)

    def onexc(func, failed, exc) -> None:
        retry(func, failed)

    def onerror(func, failed, exc_info) -> None:
        retry(func, failed)

    last: Exception | None = None
    for _ in range(5):
        try:
            if path.is_dir() and not path.is_symlink():
                if sys.version_info >= (3, 12):
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
    raise RuntimeError(
        f"Could not delete {path}. Close Git, Explorer, or any editor using ESP-Miner, then try again."
    ) from last


def csv_text(profile: dict) -> str:
    """NVS partition CSV. Uses the csv module so commas/quotes in values are safe."""

    def render(value) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        return str(value)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(("key", "type", "encoding", "value"))
    writer.writerow(("main", "namespace", "", ""))
    for key, enc, (kind, src) in NVS_ROWS:
        value = profile[src] if kind == "p" else src
        writer.writerow((key, "data", enc, render(value)))
    return buf.getvalue()


def validate_profile(profile: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not profile["hostname"]:
        errors.append("Hostname is empty.")
    elif not re.fullmatch(r"[A-Za-z0-9-]{1,32}", profile["hostname"]):
        errors.append("Hostname may only use letters, digits and '-' (max 32).")
    for key in ("stratumurl", "stratumuser"):
        if not profile[key]:
            errors.append(f"{key} is empty - the miner cannot mine without it.")
    for key in ("stratumport", "fbstratumport"):
        if not 1 <= profile[key] <= 65535:
            errors.append(f"{key} must be 1-65535.")
    for key in ("stratumdiff", "fbstratumdiff"):
        if not 1 <= profile[key] <= 65535:
            errors.append(f"{key} must be 1-65535 (stored as u16).")
    if not 0 <= profile["fanspeed"] <= 100:
        errors.append("Fan % must be 0-100.")
    if not 0 <= profile["rotation"] <= 3:
        errors.append("rotation must be 0-3.")
    if not 1 <= profile["asicfrequency"] <= 65535:
        errors.append("Frequency must be a positive u16.")
    if not 1 <= profile["asicvoltage"] <= 65535:
        errors.append("Voltage must be a positive u16.")
    if len(profile["wifipass"]) not in (0,) and not 8 <= len(profile["wifipass"]) <= 63:
        warnings.append("Wi-Fi password length is outside the WPA2 range (8-63).")
    if not profile["wifissid"]:
        warnings.append("Wi-Fi SSID is empty - the board will start in AP/setup mode.")
    if profile["asicfrequency"] > STOCK_FREQ or profile["asicvoltage"] > STOCK_VOLT:
        warnings.append(
            f"{profile['asicfrequency']} MHz / {profile['asicvoltage']} mV is above stock "
            f"({STOCK_FREQ} MHz / {STOCK_VOLT} mV). Six BM1370s can overheat."
        )
    return errors, warnings


class ScrollFrame(ttk.Frame):
    def __init__(self, parent) -> None:
        super().__init__(parent, style="Card.TFrame")
        self.canvas = tk.Canvas(self, bg=SURFACE, highlightthickness=0, borderwidth=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, style="Card.TFrame", padding=12)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _e) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.unbind_all(seq)

    def _wheel(self, e) -> None:
        if getattr(e, "num", 0) == 4:
            step = -1
        elif getattr(e, "num", 0) == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(step, "units")


class Foundry(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Selene - Gamma Hex 1300")
        self.geometry("1100x800")
        self.minsize(900, 640)
        self.configure(bg=BG)
        self.profile = dict(DEFAULT_PROFILE)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.proc: subprocess.Popen[str] | None = None
        self.stop_flag = False
        self._pulsing = False
        self._tip_shown = False
        self.workdir = tk.StringVar(value=str(Path.home() / "selene-hex1300"))
        self.firmware_dir = tk.StringVar(value="")
        self.ref = tk.StringVar(value="")
        self.own_files = tk.BooleanVar(value=False)
        self.show_pw = tk.BooleanVar(value=False)
        self.progress = tk.DoubleVar(value=0)
        self.percent_label = tk.StringVar(value="0%")
        self.status = tk.StringVar(value="Idle. Docker and ESP-IDF run on this computer, not in a browser.")
        self._entries: dict[str, ttk.Entry] = {}
        self._vars: dict[str, tk.Variable] = {}
        self._action_buttons: list[ttk.Button] = []
        self._style()
        self._build()
        self.after(50, self._drain)

    # ---------------------------------------------------------------- UI
    def _style(self) -> None:
        base = tkfont.nametofont("TkDefaultFont").copy()
        base.configure(size=10)
        self.f_base = base
        self.f_title = base.copy()
        self.f_title.configure(size=18, weight="bold")
        self.f_head = base.copy()
        self.f_head.configure(size=12, weight="bold")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=INK, font=self.f_base)
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
        style.map("Line.TButton", background=[("disabled", "#2a261f"), ("active", "#3c342b")],
                  foreground=[("disabled", MUTED)])
        style.configure("TCheckbutton", background=SURFACE, foreground=INK)
        style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", COPPER)])
        style.configure("Bar.TCheckbutton", background=BG, foreground=INK)
        style.map("Bar.TCheckbutton", background=[("active", BG)], foreground=[("active", COPPER)])
        style.configure("Vertical.TScrollbar", background=LINE, troughcolor=SURFACE, bordercolor=SURFACE,
                        arrowcolor=INK)
        style.configure(
            "Copper.Horizontal.TProgressbar",
            troughcolor=LINE, background=COPPER, bordercolor=LINE, lightcolor=COPPER, darkcolor=COPPER,
        )

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        head = ttk.Frame(outer)
        head.pack(fill="x")
        ttk.Label(head, text="SELENE", font=self.f_title).pack(side="left")
        ttk.Label(
            head,
            text="  Gamma Hex 1300 \u00b7 6\u00d7 BM1370 \u00b7 ESP-IDF v6.0.2 \u00b7 libsecp256k1",
            style="Muted.TLabel",
        ).pack(side="left", pady=6)

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True, pady=(12, 0))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=5)
        body.rowconfigure(0, weight=1)

        scroll = ScrollFrame(body)
        scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._form(scroll.inner)

        side = ttk.Frame(body)
        side.grid(row=0, column=1, sticky="nsew")
        self._actions(side)
        log_frame = ttk.Frame(side)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(
            log_frame, bg="#14120f", fg=INK, insertbackground=INK, relief="flat", wrap="word",
            font=tkfont.nametofont("TkFixedFont"), height=18, padx=10, pady=10,
        )
        log_bar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_bar.set, state="disabled")
        log_bar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        bar_row = ttk.Frame(outer)
        bar_row.pack(fill="x", pady=(6, 0))
        self.bar = ttk.Progressbar(
            bar_row, style="Copper.Horizontal.TProgressbar", maximum=100, variable=self.progress,
        )
        self.bar.pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Label(bar_row, textvariable=self.percent_label, width=5).pack(side="right")

    def _field(self, parent, label: str, key: str, row: int) -> None:
        ttk.Label(parent, text=label, style="CardMuted.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=str(self.profile.get(key, "")))
        entry = ttk.Entry(parent, textvariable=var, show="*" if "pass" in key else "")
        entry.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))
        self._vars[key] = var
        self._entries[key] = entry

    def _form(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Board profile", style="Card.TLabel", font=self.f_head).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        for index, (label, key) in enumerate(FIELDS, start=1):
            self._field(parent, label, key, index)
        flags = ttk.Frame(parent, style="Card.TFrame")
        flags.grid(row=len(FIELDS) + 1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        for index, (key, label) in enumerate(FLAGS):
            var = tk.BooleanVar(value=bool(self.profile.get(key, False)))
            self._vars[key] = var
            ttk.Checkbutton(flags, text=label, variable=var).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 10), pady=2)
        ttk.Checkbutton(parent, text="Show passwords", variable=self.show_pw, command=self._toggle_pw).grid(
            row=len(FIELDS) + 2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(
            parent,
            text=f"Stock factory profile is {STOCK_FREQ} MHz / {STOCK_VOLT} mV. "
                 "Higher settings can overheat six BM1370s.",
            style="CardMuted.TLabel", wraplength=300,
        ).grid(row=len(FIELDS) + 3, column=0, columnspan=2, sticky="w", pady=(8, 0))

    def _toggle_pw(self) -> None:
        for key, entry in self._entries.items():
            if "pass" in key:
                entry.configure(show="" if self.show_pw.get() else "*")

    def _actions(self, parent) -> None:
        work_row = ttk.Frame(parent)
        work_row.pack(fill="x")
        ttk.Label(work_row, text="Workdir", width=12).pack(side="left")
        ttk.Entry(work_row, textvariable=self.workdir).pack(side="left", fill="x", expand=True, padx=(8, 0))
        b = ttk.Button(work_row, text="Folder", style="Line.TButton", command=self._pick_dir)
        b.pack(side="left", padx=(8, 0))
        self._action_buttons.append(b)

        ref_row = ttk.Frame(parent)
        ref_row.pack(fill="x", pady=(8, 0))
        ttk.Label(ref_row, text="Tag / branch", width=12).pack(side="left")
        ttk.Entry(ref_row, textvariable=self.ref).pack(side="left", fill="x", expand=True, padx=(8, 0))
        ttk.Checkbutton(ref_row, text="Own files (Linux/WSL)", variable=self.own_files,
                        style="Bar.TCheckbutton").pack(side="left", padx=(8, 0))

        firmware_row = ttk.Frame(parent)
        firmware_row.pack(fill="x", pady=(8, 0))
        ttk.Label(firmware_row, text="New firmware", width=12).pack(side="left")
        ttk.Entry(firmware_row, textvariable=self.firmware_dir).pack(side="left", fill="x", expand=True, padx=(8, 0))
        b = ttk.Button(firmware_row, text="Folder", style="Line.TButton", command=self._pick_firmware)
        b.pack(side="left", padx=(8, 0))
        self._action_buttons.append(b)
        b = ttk.Button(firmware_row, text="Replace ESP-Miner", command=self.copy_firmware)
        b.pack(side="left", padx=(8, 0))
        self._action_buttons.append(b)

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
            btn = ttk.Button(buttons, text=label, style=style, command=command)
            btn.grid(row=index // 2, column=index % 2, sticky="ew", padx=4, pady=4)
            if label != "Stop":
                self._action_buttons.append(btn)
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

    def _pick_dir(self) -> None:
        chosen = filedialog.askdirectory(
            title="Hexforge working directory", initialdir=self.workdir.get() or str(Path.home()))
        if chosen:
            self.workdir.set(chosen)

    def _pick_firmware(self) -> None:
        start = self.firmware_dir.get() or str(Path.home())
        chosen = filedialog.askdirectory(title="Folder with the new firmware", initialdir=start)
        if chosen:
            self.firmware_dir.set(chosen)  # selecting never deletes anything; press Replace ESP-Miner

    # ------------------------------------------------------------ plumbing
    def repo(self) -> Path:
        return Path(self.workdir.get()).expanduser() / "ESP-Miner"

    def log_line(self, text: str) -> None:
        self.events.put(("log", text))

    def set_status(self, text: str) -> None:
        self.events.put(("status", text))

    def set_progress(self, percent: float) -> None:
        self.events.put(("progress", percent))

    def pulse(self, on: bool) -> None:
        self.events.put(("pulse", on))

    def _set_busy_ui(self, busy: bool) -> None:
        for btn in self._action_buttons:
            btn.configure(state="disabled" if busy else "normal")

    def _drain(self) -> None:
        lines: list[str] = []
        try:
            for _ in range(MAX_EVENTS_PER_TICK):
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    lines.append(str(payload))
                elif kind == "status":
                    self.status.set(str(payload))
                elif kind == "pulse":
                    self._set_pulse(bool(payload))
                elif kind == "progress":
                    if self._pulsing:
                        self._set_pulse(False)
                    value = max(0.0, min(100.0, float(payload)))  # type: ignore[arg-type]
                    self.progress.set(value)
                    self.percent_label.set(f"{int(round(value))}%")
                elif kind == "done":
                    self.worker = None
                    if self._pulsing:
                        self._set_pulse(False)
                    self._set_busy_ui(False)
        except queue.Empty:
            pass
        if lines:
            self.log.configure(state="normal")
            self.log.insert("end", "\n".join(lines) + "\n")
            total = int(self.log.index("end-1c").split(".")[0])
            if total > MAX_LOG_LINES:
                self.log.delete("1.0", f"{total - MAX_LOG_LINES + 1}.0")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(50, self._drain)

    def _set_pulse(self, on: bool) -> None:
        if on and not self._pulsing:
            self.bar.configure(mode="indeterminate")
            self.bar.start(15)
            self.percent_label.set("...")
        elif not on and self._pulsing:
            self.bar.stop()
            self.bar.configure(mode="determinate")
        self._pulsing = on

    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start(self, target, status: str) -> None:
        if self.busy():
            messagebox.showinfo("Selene", "A step is already running.")
            return
        self.stop_flag = False
        self.progress.set(0)
        self.percent_label.set("0%")
        self.set_status(status)
        self._set_busy_ui(True)
        self.worker = threading.Thread(target=self._guard, args=(target,), daemon=True)
        self.worker.start()

    def _guard(self, target) -> None:
        try:
            target()
        except StopRequested:
            self.log_line("stopped by user")
            self.set_status("Stopped.")
        except Exception as exc:  # noqa: BLE001 - surface toolchain failures in the log
            if self.stop_flag:
                self.log_line("stopped by user")
                self.set_status("Stopped.")
            else:
                self.log_line(f"error: {exc}")
                self.set_status("Stopped on an error. See the log.")
        finally:
            self.events.put(("done", ""))

    def _check_stop(self) -> None:
        if self.stop_flag:
            raise StopRequested()

    def request_stop(self) -> None:
        if not self.busy():
            self.set_status("Nothing is running.")
            return
        self.stop_flag = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            self._kill_proc(proc)
        threading.Thread(target=self._kill_container, daemon=True).start()
        self.set_status("Stop requested.")

    @staticmethod
    def _kill_proc(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True,
                               creationflags=NO_WINDOW, timeout=10)
            else:
                proc.terminate()
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    def _kill_container(self) -> None:
        if shutil.which("docker"):
            try:
                subprocess.run(["docker", "kill", BUILD_NAME], capture_output=True,
                               creationflags=NO_WINDOW, timeout=20)
            except Exception:  # noqa: BLE001
                pass

    def capture(self, args: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str]:
        try:
            done = subprocess.run(
                args, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired):
            return 1, ""
        return done.returncode, (done.stdout or "") + (done.stderr or "")

    def run_cmd(self, args: list[str], cwd: Path | None = None) -> int:
        self._check_stop()
        shown = " ".join(args)
        self.log_line("$ " + shown)
        proc = subprocess.Popen(
            args, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
        )
        self.proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                clean = ANSI_RE.sub("", line.rstrip())
                if clean:
                    self.log_line(clean)
                if self.stop_flag:
                    self._kill_proc(proc)
                    break
            code = proc.wait()
        finally:
            self.proc = None
        self._check_stop()
        if code != 0:
            raise RuntimeError(f"command failed ({code}): {shown}")
        return code

    def git(self, args: list[str], cwd: Path | None = None) -> int:
        # safe.directory=* on the command line only; never touches ~/.gitconfig
        return self.run_cmd(["git", "-c", "safe.directory=*", *args], cwd=cwd)

    def require_tool(self, name: str) -> None:
        if shutil.which(name) is None:
            raise RuntimeError(f"{name} is not on PATH. Install it, then run this step again.")

    def perf_tip(self) -> None:
        if os.name == "nt" and not self._tip_shown:
            self._tip_shown = True
            self.log_line(
                "tip: bind-mounting a Windows folder into Docker is slow. For much faster builds, "
                "keep the workdir inside WSL2 (run this app there) or on a Docker volume."
            )

    # ------------------------------------------------------- firmware swap
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
        if not messagebox.askyesno(
            "Selene - confirm replace",
            f"This DELETES everything inside:\n\n{dest}\n\n(including .git) and copies in:\n\n{source}\n\nContinue?",
            icon="warning",
        ):
            return

        def work() -> None:
            self._replace_esp_miner(source, dest)

        self.start(work, "Replacing ESP-Miner with the selected firmware...")

    def _replace_esp_miner(self, source: Path, dest: Path) -> None:
        source = source.resolve()
        dest.mkdir(parents=True, exist_ok=True)
        dest = dest.resolve()
        if source == dest or dest.is_relative_to(source) or source.is_relative_to(dest):
            raise RuntimeError("The firmware folder cannot be ESP-Miner, inside it, or contain it.")
        files: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(source):
            at_root = Path(dirpath) == source
            dirnames[:] = [d for d in dirnames
                           if d not in IGNORE_DIRS_ANYWHERE and not (at_root and d == "build")]
            files.extend(Path(dirpath) / name for name in filenames)
        if not files:
            raise RuntimeError("That folder has no files to copy.")
        children = list(dest.iterdir())
        self.log_line(f"clearing {dest}")
        for index, child in enumerate(children, start=1):
            self._check_stop()
            _remove_path(child)
            self.log_line(f"removed {child.name}")
            self.set_progress(20 * index / len(children))
        total = len(files)
        for index, src in enumerate(files, start=1):
            self._check_stop()
            rel = src.relative_to(source)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            if index == 1 or index == total or index % 50 == 0:
                self.log_line(f"copied {rel.as_posix()}")
                self.set_progress(20 + 80 * index / total)
        self.set_progress(100)
        self.log_line(f"replaced ESP-Miner with {total} files from {source}")
        self.set_status("ESP-Miner now contains only the selected firmware.")

    # ------------------------------------------------------------- profile
    def read_profile(self) -> dict:
        profile = dict(self.profile)
        for key, var in self._vars.items():
            value = var.get()
            if key in BOOL_KEYS:
                profile[key] = bool(value)
            elif key in INT_KEYS:
                try:
                    profile[key] = int(str(value).strip())
                except ValueError as exc:
                    raise ValueError(f"{key} must be a whole number") from exc
            else:
                text = str(value).strip()
                if "\n" in text or "\r" in text:
                    raise ValueError(f"{key} contains a line break")
                profile[key] = text
        for key in ("overheatMode", "fbstratumxnsub", "rotation"):
            profile.setdefault(key, DEFAULT_PROFILE[key])
        self.profile = profile
        return profile

    def checked_profile(self) -> dict | None:
        """Main-thread only: parse, validate, and confirm risky settings."""
        try:
            profile = self.read_profile()
        except ValueError as exc:
            messagebox.showerror("Selene", f"Profile problem: {exc}")
            return None
        errors, warnings = validate_profile(profile)
        if errors:
            messagebox.showerror("Selene", "Fix these first:\n\n- " + "\n- ".join(errors))
            return None
        if warnings and not messagebox.askyesno(
            "Selene - check profile", "Warnings:\n\n- " + "\n- ".join(warnings) + "\n\nContinue anyway?",
            icon="warning",
        ):
            return None
        return profile

    def _write_config(self, root: Path, profile: dict) -> Path:
        path = root / "configs" / CONFIG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(csv_text(profile), encoding="utf-8", newline="\n")
        self.log_line(f"wrote {path}")
        exclude = root / ".git" / "info" / "exclude"
        try:
            if exclude.parent.is_dir():
                existing = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
                entry = f"configs/{CONFIG_NAME}"
                if entry not in existing.splitlines():
                    with exclude.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(("" if existing.endswith("\n") or not existing else "\n") + entry + "\n")
                    self.log_line("added the config to .git/info/exclude (it holds Wi-Fi/pool secrets)")
        except OSError:
            pass
        self.verify_keys(root)
        self.log_line("boardversion=1300 devicemodel=GammaHex asicmodel=BM1370")
        return path

    def verify_keys(self, root: Path) -> None:
        texts = []
        for rel in ("main/nvs_config.h", "main/nvs_config.c"):
            f = root / rel
            if f.is_file():
                texts.append(f.read_text(encoding="utf-8", errors="replace"))
        if not texts:
            self.log_line("key check skipped: main/nvs_config.h not found in this tree")
            return
        known = set(re.findall(r'"([A-Za-z0-9_]{1,15})"', "\n".join(texts)))
        missing = [key for key, _enc, _src in NVS_ROWS if key not in known]
        if missing:
            self.log_line("warning: keys not found in main/nvs_config.*: " + ", ".join(missing))
            self.log_line("  firmware ignores unknown NVS keys - compare against your ESP-Miner version")
        else:
            self.log_line("key check: all NVS keys exist in this ESP-Miner tree")

    # --------------------------------------------------------------- steps
    def check_tools(self) -> None:
        def work() -> None:
            self.log_line("--- host prerequisites ---")
            for name in ("git", "docker", "bitaxetool"):
                self.log_line(f"{name}: {shutil.which(name) or 'MISSING'}")
            if shutil.which("docker") is None:
                self.set_status("Docker is missing. Install Docker Desktop or Engine, then check again.")
                return
            self.run_cmd(["docker", "info", "--format", "daemon {{.ServerVersion}}"])
            self.log_line(f"ESP-IDF image base: {IDF_BASE}")
            self.log_line(f"libsecp256k1: {SECP_URL}")
            self.log_line(f"submodule path: {SECP_REL.as_posix()}")
            self.perf_tip()
            if shutil.which("bitaxetool") is None:
                self.log_line("install the flasher with: pip install --upgrade bitaxetool")
            self.set_progress(100)
            self.set_status("Host tools look usable. Next: clone ESP-Miner.")

        self.start(work, "Checking git, Docker and bitaxetool...")

    def pinned_secp_sha(self, root: Path) -> str | None:
        if not (root / ".git").exists():
            return None
        code, out = self.capture(["git", "-c", "safe.directory=*", "ls-tree", "HEAD", SECP_REL.as_posix()], cwd=root)
        match = re.match(r"160000 commit ([0-9a-f]{40})", out) if code == 0 else None
        return match.group(1) if match else None

    def ensure_secp(self, root: Path) -> None:
        header = root / SECP_HEADER
        if header.is_file():
            self.log_line(f"libsecp256k1 header present: {header}")
            return
        if (root / ".git").exists():
            self.log_line("libsecp256k1 missing - initializing submodules")
            try:
                self.git(["submodule", "update", "--init", "--recursive", "--depth", "1"], cwd=root)
            except StopRequested:
                raise
            except RuntimeError as exc:
                self.log_line(f"submodule update failed ({exc}); falling back to a direct clone")
            if header.is_file():
                self.log_line(f"libsecp256k1 header present: {header}")
                return
        else:
            self.log_line("no git metadata in this tree - fetching libsecp256k1 directly")
        dest = root / SECP_REL
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            _remove_path(dest)
        sha = self.pinned_secp_sha(root)
        if sha:
            self.log_line(f"cloning bitcoin-core/secp256k1 at the pinned commit {sha[:12]}")
            self.git(["clone", SECP_URL, str(dest)])
            self.git(["checkout", "--detach", sha], cwd=dest)
        else:
            self.log_line("no pinned commit known - cloning the default branch (shallow)")
            self.git(["clone", "--depth", "1", SECP_URL, str(dest)])
        if not header.is_file():
            raise RuntimeError(f"secp256k1.h was not found at {header}")
        self.log_line(f"libsecp256k1 header present: {header}")

    def ensure_source(self) -> Path:
        root = self.repo()
        root.parent.mkdir(parents=True, exist_ok=True)
        ref = self.ref.get().strip()
        if (root / "CMakeLists.txt").is_file() or (root / ".git").is_dir():
            self.log_line(f"using existing ESP-Miner at {root}")
            if ref:
                self.log_line(f"note: tag/branch '{ref}' ignored - the tree already exists (delete it to re-clone)")
        elif root.exists() and any(root.iterdir()):
            raise RuntimeError(f"{root} is not empty and is not an ESP-Miner tree.")
        else:
            cmd = ["clone", "--recursive", "--shallow-submodules"]
            if ref:
                cmd += ["--branch", ref]
            self.git([*cmd, REPO_URL, str(root)])
        return root

    def clone_repo(self) -> None:
        def work() -> None:
            self.require_tool("git")
            self.pulse(True)
            root = self.ensure_source()
            self.ensure_secp(root)
            self.set_progress(100)
            self.set_status("Source and libsecp256k1 are ready.")

        self.start(work, "Preparing ESP-Miner and libsecp256k1...")

    def ensure_image(self, root: Path, force: bool) -> None:
        dockerfile = root / ".devcontainer" / "Dockerfile"
        if not dockerfile.is_file():
            raise RuntimeError("Clone ESP-Miner first - .devcontainer/Dockerfile is missing.")
        data = dockerfile.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]
        base = "unknown"
        for line in data.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if parts and parts[0].upper() == "FROM":
                names = [p for p in parts[1:] if not p.startswith("--")]
                if names:
                    base = names[0]
                break
        self.log_line(f"Dockerfile base image: {base}")
        if base != IDF_BASE:
            self.log_line(f"note: expected {IDF_BASE}; the checkout's Dockerfile decides the real IDF version")
        if not force:
            code, out = self.capture(
                ["docker", "image", "inspect", "-f", '{{ index .Config.Labels "selene.dockerfile" }}', IMAGE])
            if code == 0 and out.strip() == digest:
                self.log_line(f"image {IMAGE} is current (Dockerfile {digest}) - skipping rebuild")
                return
        self.pulse(True)
        self.run_cmd([
            "docker", "build", "--progress=plain", "--label", f"selene.dockerfile={digest}",
            "-t", IMAGE, str(dockerfile.parent),
        ])

    def build_image(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            self.ensure_image(self.repo(), force=True)
            self.set_progress(100)
            self.set_status("IDF image is built. Next: compile.")

        self.start(work, "Building the ESP-IDF Docker image. The first pull is large.")

    def write_config(self) -> None:
        profile = self.checked_profile()
        if profile is None:
            return
        root = self.repo()
        if not root.is_dir():
            messagebox.showerror("Selene", "Clone ESP-Miner before writing the config into it.")
            return
        self._write_config(root, profile)
        self.set_status("Hex 1300 NVS config written. Flash it with bitaxetool after the merge.")

    def docker_idf(self, script: str) -> None:
        root = self.repo()
        if not (root / "CMakeLists.txt").is_file():
            raise RuntimeError("Clone ESP-Miner first.")
        self.ensure_secp(root)
        self.capture(["docker", "rm", "-f", BUILD_NAME], timeout=20)
        args = ["docker", "run", "--rm", "--name", BUILD_NAME,
                "-v", f"{root.resolve()}:/workspace", "-w", "/workspace"]
        if self.own_files.get() and hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
        else:
            args += ["-v", f"{CCACHE_VOL}:/ccache", "-e", "CCACHE_DIR=/ccache", "-e", "IDF_CCACHE_ENABLE=1",
                     "-v", f"{NPM_VOL}:/npm-cache", "-e", "npm_config_cache=/npm-cache"]
        prelude = '[ -f "$IDF_PATH/export.sh" ] && . "$IDF_PATH/export.sh" >/dev/null 2>&1; '
        args += [IMAGE, "bash", "-c", prelude + script]
        self.run_cmd(args)

    def compile_firmware(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            self.perf_tip()
            self.log_line("idf.py build - AxeOS is bundled by the IDF build")
            self.pulse(True)
            self.docker_idf("git config --global --add safe.directory /workspace && idf.py build")
            self.set_progress(100)
            self.set_status("Firmware compiled. Next: merge a factory image.")

        self.start(work, "Compiling inside ESP-IDF. This takes several minutes.")

    def _merge(self) -> Path:
        merged = self.repo() / "esp-miner-merged.bin"
        if merged.exists():
            merged.unlink()
        self.pulse(True)
        self.docker_idf("chmod +x ./merge_bin.sh && ./merge_bin.sh ./esp-miner-merged.bin")
        if not merged.is_file() or merged.stat().st_size == 0:
            raise RuntimeError(f"merge finished but {merged} was not produced")
        self.log_line(f"factory image: {merged} ({merged.stat().st_size // 1024} KiB)")
        return merged

    def merge_bin(self) -> None:
        def work() -> None:
            self.require_tool("docker")
            self._merge()
            self.log_line("flash when the board is on USB:")
            self.log_line(f"bitaxetool --config ./configs/{CONFIG_NAME} --firmware ./esp-miner-merged.bin")
            self.set_progress(100)
            self.set_status("Factory image is ready. Selene does not flash unless you run bitaxetool.")

        self.start(work, "Merging bootloader, partition table, and app...")

    def run_all(self) -> None:
        profile = self.checked_profile()
        if profile is None:
            return

        def work() -> None:
            self.require_tool("git")
            self.require_tool("docker")
            self.perf_tip()
            self.set_progress(5)
            self.run_cmd(["docker", "info", "--format", "daemon {{.ServerVersion}}"])
            root = self.ensure_source()
            self.set_progress(25)
            self.ensure_secp(root)
            self.set_progress(35)
            self.ensure_image(root, force=False)
            self.set_progress(60)
            self._write_config(root, profile)
            self.set_progress(70)
            self.log_line("compiling firmware...")
            self.pulse(True)
            self.docker_idf("git config --global --add safe.directory /workspace && idf.py build")
            self.set_progress(90)
            self._merge()
            self.log_line("done. install the flasher with: pip install --upgrade bitaxetool")
            self.log_line(f"flash: bitaxetool --config ./configs/{CONFIG_NAME} --firmware ./esp-miner-merged.bin")
            self.set_progress(100)
            self.set_status("Build finished. Factory bin is esp-miner-merged.bin inside the ESP-Miner folder.")

        self.start(work, "Running the full Hex 1300 toolchain...")


def main() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    Foundry().mainloop()


if __name__ == "__main__":
    main()
