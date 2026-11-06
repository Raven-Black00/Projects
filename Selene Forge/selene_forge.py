#!/usr/bin/env python3
"""SELENE — Windows forge for Bitaxe Gamma Hex board 1300.

Requires Python 3.9+ (tkinter ships with the official Windows installer),
Git, and Docker Desktop. Double-click Selene.bat or run:  py -3 selene_forge.py
"""

from __future__ import annotations

import os
import queue
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path

SECP_URL = "https://github.com/bitcoin-core/secp256k1.git"
SECP_REL = Path("components") / "libsecp256k1" / "libsecp256k1"
IMAGE = "espminer-build"
FACTORY = "esp-miner-factory-1300.bin"
IDF_FALLBACK = "espressif/idf:v6.0.2"
FORGE = "5"
NVS_GEN = "/opt/esp/idf/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
BOARD_RECORDS = (Path("configs") / "config-1300.csv", Path("config-1300.cvs"))


def find_board_record(project: Path) -> Path | None:
    for relative in BOARD_RECORDS:
        if (project / relative).is_file():
            return relative
    return None


def idf_build_command(record: Path) -> str:
    posix = record.as_posix()
    return (
        "git config --global --add safe.directory /workspace && "
        f"{NVS_GEN} generate {posix} config.bin 0x6000 && "
        f"idf.py build && bash ./merge_bin.sh -c {FACTORY}"
    )

BG = "#0b0a08"
SURFACE = "#14110d"
RAISED = "#1e1914"
FG = "#f3e6c4"
GOLD = "#d4b36a"
MUTED = "#9a8b72"
LINE = "#3d3428"
EMBER = "#c45c3e"
RUNES = "ᚠᚢᚦᚨᚱᚲᚷᚹᚺᚾᛁᛃᛇᛈᛉᛊᛏᛒᛖᛗᛚᛜᛞᛟ"

WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if WIN else 0
PHASES = (
    ("Docker Desktop", 10),
    ("Purge temp", 8),
    ("Stage source", 14),
    ("libsecp256k1", 16),
    ("Build image", 16),
    ("idf.py build", 28),
    ("Factory 1300", 8),
)
PHASE_TOTAL = sum(weight for _, weight in PHASES)


def output_settings() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or str(Path.home())
    return Path(base) / "selene" / "output.txt"


def load_output() -> Path | None:
    path = output_settings()
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip().strip('"')
    return Path(text) if text else None


def save_output(path: Path) -> None:
    settings = output_settings()
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(str(path), encoding="utf-8")


def inside_temp(path: Path) -> bool:
    try:
        path.resolve().relative_to(temp_root().resolve())
    except ValueError:
        return False
    return True


def deliver_build(project: Path, output: Path | None) -> list[Path]:
    found = [
        project / name
        for name in (FACTORY, "esp-miner-merged.bin", str(Path("build") / "esp-miner.bin"))
        if (project / name).is_file()
    ]
    if output is None:
        return found
    output.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in found:
        target = output / source.name
        shutil.copy2(source, target)
        copied.append(target)
    return copied


def temp_root() -> Path:
    base = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home())
    return Path(base) / "selene"


def work_dir() -> Path:
    return temp_root() / "gamma-hex-1300"


def percent_for(index: int, fraction: float) -> int:
    before = sum(weight for _, weight in PHASES[:index])
    current = PHASES[index][1] if 0 <= index < len(PHASES) else 0
    fraction = max(0.0, min(1.0, fraction))
    return max(0, min(100, round((before + current * fraction) / PHASE_TOTAL * 100)))


def file_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def _make_writable(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def force_remove(path: Path) -> None:
    """Delete a tree. Git pack files are read-only on Windows and block rmtree."""
    if not path.exists() and not path.is_symlink():
        return

    def retry(func, failed, _exc) -> None:
        _make_writable(failed)
        func(failed)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=lambda func, failed, exc: retry(func, failed, exc))


def wipe_temp() -> int:
    root = temp_root()
    count = file_count(root)
    if root.exists():
        try:
            force_remove(root)
        except OSError:
            leftover = root.with_name(f"selene-locked-{os.getpid()}")
            if leftover.exists():
                force_remove(leftover)
            root.rename(leftover)
            try:
                force_remove(leftover)
            except OSError:
                pass
    work_dir().mkdir(parents=True, exist_ok=True)
    return count


def _safe_relative(name: str) -> Path | None:
    cleaned = name.replace("\\", "/").lstrip("/")
    if not cleaned or cleaned.endswith("/"):
        return None
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return Path(*parts)


def extract_archive(archive: Path, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    lower = archive.name.lower()
    written = 0
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.filename.endswith("/") and _safe_relative(info.filename) is None:
                    continue
                rel = _safe_relative(info.filename)
                if rel is None:
                    raise ValueError(f"Blocked path in archive: {info.filename}")
                target = dest / rel
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                written += 1
        return written
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".gz")):
        mode = "r:gz" if lower.endswith((".tar.gz", ".tgz", ".gz")) else "r:"
        with tarfile.open(archive, mode) as bundle:
            for member in bundle.getmembers():
                if member.issym() or member.islnk():
                    raise ValueError(f"Blocked link in archive: {member.name}")
                if member.isdir() and _safe_relative(member.name) is None:
                    continue
                rel = _safe_relative(member.name)
                if rel is None:
                    raise ValueError(f"Blocked path in archive: {member.name}")
                target = dest / rel
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = bundle.extractfile(member)
                if extracted is None:
                    continue
                with extracted, target.open("wb") as sink:
                    shutil.copyfileobj(extracted, sink)
                written += 1
        return written
    raise ValueError("Use a .zip, .tar, or .tar.gz archive.")


def copy_directory(source: Path, dest: Path) -> tuple[int, int]:
    written = 0
    skipped = 0
    source = source.resolve()
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        current = Path(dirpath)
        relative = current.relative_to(source)
        (dest / relative).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name == ".git":
                continue
            target = dest / relative / name
            try:
                if target.exists():
                    _make_writable(str(target))
                shutil.copy2(current / name, target)
                written += 1
            except PermissionError:
                skipped += 1
    return written, skipped


def locate_project(dest: Path) -> Path:
    markers = (".devcontainer", "CMakeLists.txt", "merge_bin.sh")

    def is_root(path: Path) -> bool:
        return any((path / marker).exists() for marker in markers)

    if is_root(dest):
        return dest
    dirs = [path for path in dest.iterdir() if path.is_dir() and path.name != ".git"]
    if len(dirs) == 1 and is_root(dirs[0]):
        return dirs[0]
    if len(dirs) == 1:
        nested = [path for path in dirs[0].iterdir() if path.is_dir() and path.name != ".git"]
        if len(nested) == 1 and is_root(nested[0]):
            return nested[0]
    return dest


def secp_present(root: Path) -> bool:
    base = root / SECP_REL
    if (base / "include" / "secp256k1.h").is_file():
        return True
    if not base.exists():
        return False
    return any(base.rglob("secp256k1.h")) or any(base.rglob("secp256k1.c"))


def docker_desktop_exe() -> Path | None:
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    names = (
        Path("Docker") / "Docker" / "Docker Desktop.exe",
        Path("Docker Desktop.exe"),
    )
    for root in roots:
        if not root:
            continue
        for name in names:
            candidate = Path(root) / name
            if candidate.is_file():
                return candidate
    return None


def run_command(args: list[str], cwd: Path | None = None, timeout: float | None = None) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
    except FileNotFoundError:
        return 127, f"{args[0]}: command not found"
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") + (exc.stderr or "")
        return 124, (out or "timed out").strip()
    text = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return completed.returncode, text


def docker_engine_version() -> str | None:
    code, text = run_command(["docker", "version", "--format", "{{.Server.Version}}"], timeout=8)
    if code == 0 and text and "error" not in text.lower() and "cannot connect" not in text.lower():
        return text.split()[0]
    return None


def wake_docker() -> tuple[bool, str]:
    version = docker_engine_version()
    if version:
        return True, f"Docker engine {version} is running."
    _code, text = run_command(["docker", "desktop", "start"], timeout=25)
    notes = [text] if text else []
    exe = docker_desktop_exe()
    if exe is not None:
        subprocess.Popen(  # fixed Docker Desktop path, started in the background
            [str(exe)],
            creationflags=(0x00000008 | 0x00000200) if WIN else 0,
            close_fds=True,
        )
        notes.append(f"Started {exe.name} in the background.")
    version = docker_engine_version()
    if version:
        return True, f"Docker Desktop is running (engine {version})."
    if exe is None and _code == 127:
        return False, "Docker is not installed. Install Docker Desktop for Windows, then forge again."
    if notes:
        return False, notes[-1] + " Engine is still down."
    return False, "Docker Desktop did not answer."


def popen_stream(args: list[str], cwd: Path, stop: threading.Event, on_line=None) -> int:
    process = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=NO_WINDOW,
    )
    assert process.stdout is not None
    for raw in process.stdout:
        if stop.is_set():
            process.terminate()
            break
        line = raw.rstrip("\r\n")
        if on_line is not None:
            on_line(line)
    return process.wait()


def self_test() -> int:
    scratch = Path(tempfile.mkdtemp(prefix="selene-test-"))
    try:
        sample = scratch / "tree"
        (sample / "main").mkdir(parents=True)
        (sample / "main" / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
        archive = scratch / "src.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(sample / "main" / "main.c", "esp-miner/main/main.c")
            bundle.writestr("../outside.txt", "nope")
        dest = scratch / "out"
        try:
            extract_archive(archive, dest)
        except ValueError:
            pass
        else:
            print("zip slip was not blocked", file=sys.stderr)
            return 1
        if (scratch / "outside.txt").exists() or (dest / "outside.txt").exists():
            print("zip slip wrote a file", file=sys.stderr)
            return 1
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.write(sample / "main" / "main.c", "esp-miner/main/main.c")
        count = extract_archive(archive, dest)
        if count != 1 or not (dest / "esp-miner" / "main" / "main.c").is_file():
            print("extract failed", file=sys.stderr)
            return 1
        if secp_present(dest):
            print("secp false positive", file=sys.stderr)
            return 1
        os.environ["TEMP"] = str(scratch / "win-temp")
        if wipe_temp() != 0 or not work_dir().is_dir():
            print("wipe failed", file=sys.stderr)
            return 1
        marker = work_dir() / "keep.txt"
        marker.write_text("x", encoding="utf-8")
        if wipe_temp() != 1 or marker.exists():
            print("second wipe failed", file=sys.stderr)
            return 1
        copied, skipped = copy_directory(sample, work_dir() / "src")
        if copied != 1 or skipped != 0 or not (work_dir() / "src" / "main" / "main.c").is_file():
            print("copy failed", file=sys.stderr)
            return 1
        locked = work_dir() / "components" / "libsecp256k1" / "libsecp256k1" / ".git" / "objects" / "pack"
        locked.mkdir(parents=True)
        idx = locked / "pack-c7c7765e74a962db22a8627ef5bc451b40446f05.idx"
        idx.write_text("readonly", encoding="utf-8")
        os.chmod(idx, stat.S_IREAD)
        os.chmod(locked, stat.S_IREAD | stat.S_IEXEC)
        if wipe_temp() < 1 or idx.exists():
            print("readonly wipe failed", file=sys.stderr)
            return 1
        wrapped = scratch / "zip-root" / "ESP-Miner-master"
        (wrapped / ".devcontainer").mkdir(parents=True)
        (wrapped / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.16)\n", encoding="utf-8")
        if locate_project(scratch / "zip-root") != wrapped:
            print("project root not found", file=sys.stderr)
            return 1
        os.environ["APPDATA"] = str(scratch / "appdata")
        save_output(scratch / "firmware")
        if load_output() != scratch / "firmware":
            print("output setting failed", file=sys.stderr)
            return 1
        project = scratch / "proj"
        (project / "build").mkdir(parents=True)
        (project / FACTORY).write_bytes(b"factory")
        (project / "build" / "esp-miner.bin").write_bytes(b"app")
        copied = deliver_build(project, scratch / "firmware")
        names = {path.name for path in copied}
        if names != {FACTORY, "esp-miner.bin"}:
            print("deliver missed a binary", file=sys.stderr)
            return 1
        if (scratch / "firmware" / FACTORY).read_bytes() != b"factory":
            print("deliver copy failed", file=sys.stderr)
            return 1
        if not inside_temp(work_dir() / "bin"):
            print("temp guard failed", file=sys.stderr)
            return 1
        tree = scratch / "tree-cvs"
        tree.mkdir()
        (tree / "config-1300.cvs").write_text("board", encoding="utf-8")
        if find_board_record(tree) != Path("config-1300.cvs"):
            print("did not find config-1300.cvs", file=sys.stderr)
            return 1
        if "config-1300.cvs" not in idf_build_command(Path("config-1300.cvs")) or "merge_bin.sh -c" not in idf_build_command(Path("config-1300.csv")):
            print("factory command does not stamp board 1300", file=sys.stderr)
            return 1
        if percent_for(0, 1) != 10 or percent_for(len(PHASES) - 1, 1) != 100:
            print("progress math failed", file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print("selene self-test ok")
    return 0


class ForgeApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog

        self.tk = tk
        self.filedialog = filedialog
        self.root = tk.Tk()
        self.root.title("SELENE — Gamma Hex 1300")
        self.root.geometry("1100x740")
        self.root.minsize(860, 620)
        self.root.configure(bg=BG)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.stop = threading.Event()
        self.source_kind = tk.StringVar(value="directory")
        self.source_path: Path | None = None
        self.output_path = load_output()
        self.percent = 0
        self.phase = tk.StringVar(value="Idle — board 1300")
        self.docker_text = tk.StringVar(value="Checking")
        self.busy = False
        self.history: list[str] = []
        self.history_at = 0
        self._build()
        self.write("Microsoft Windows", "muted")
        self.write(f"SELENE FORGE {FORGE}  ·  Bitaxe Gamma Hex  ·  board 1300", "gold")
        self.write(f"Temp vault: {temp_root()}", "muted")
        if self.output_path:
            self.write(f"Output folder: {self.output_path}", "gold")
        else:
            self.write("Choose an output folder. Finished binaries are copied there.", "muted")
        self.write("Type help. Forge builds only after a source is staged.", "muted")
        self.root.after(200, self._pump)
        threading.Thread(target=self._probe_docker, daemon=True).start()

    def _build(self) -> None:
        tk = self.tk
        tk.Label(self.root, text=RUNES * 4, bg=BG, fg=GOLD, font=("Consolas", 9)).pack(fill="x", pady=(6, 0))

        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=16, pady=8)
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="ᛟ  GAMMA HEX  ᛟ", bg=BG, fg=GOLD, font=("Georgia", 10)).pack(anchor="w")
        tk.Label(titles, text="SELENE", bg=BG, fg=GOLD, font=("Georgia", 28)).pack(anchor="w")
        tk.Label(
            titles,
            text="Windows Python forge for Bitaxe Gamma Hex, board 1300.",
            bg=BG,
            fg=MUTED,
            font=("Consolas", 10),
        ).pack(anchor="w")
        badge = tk.Frame(header, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        badge.pack(side="right", padx=(12, 0))
        tk.Label(badge, text="DOCKER DESKTOP", bg=SURFACE, fg=MUTED, font=("Consolas", 8)).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Label(badge, textvariable=self.docker_text, bg=SURFACE, fg=FG, font=("Consolas", 11)).pack(anchor="w", padx=10, pady=(0, 8))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        side = tk.Frame(body, bg=BG, width=280)
        side.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        side.grid_propagate(False)

        source = tk.Frame(side, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        source.pack(fill="x")
        tk.Label(source, text="SOURCE", bg=SURFACE, fg=GOLD, font=("Georgia", 11)).pack(anchor="w", padx=12, pady=(10, 4))
        kind_row = tk.Frame(source, bg=SURFACE)
        kind_row.pack(fill="x", padx=12)
        for value, label in (("directory", "Directory"), ("archive", "Archive")):
            tk.Radiobutton(
                kind_row,
                text=label,
                variable=self.source_kind,
                value=value,
                bg=SURFACE,
                fg=FG,
                selectcolor=RAISED,
                activebackground=SURFACE,
                activeforeground=GOLD,
                font=("Consolas", 10),
            ).pack(side="left", padx=(0, 8))
        self.path_label = tk.Label(
            source,
            text="Nothing staged yet.",
            bg=SURFACE,
            fg=MUTED,
            font=("Consolas", 9),
            wraplength=250,
            justify="left",
        )
        self.path_label.pack(anchor="w", padx=12, pady=8)
        self._button(source, "Choose source", self.choose_source).pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(source, text="OUTPUT", bg=SURFACE, fg=GOLD, font=("Georgia", 11)).pack(anchor="w", padx=12, pady=(4, 4))
        self.output_label = tk.Label(
            source,
            text=str(self.output_path) if self.output_path else "Not selected.",
            bg=SURFACE,
            fg=FG if self.output_path else MUTED,
            font=("Consolas", 9),
            wraplength=250,
            justify="left",
        )
        self.output_label.pack(anchor="w", padx=12, pady=(0, 8))
        self._button(source, "Choose output", self.choose_output).pack(fill="x", padx=12, pady=(0, 12))

        rite = tk.Frame(side, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        rite.pack(fill="both", expand=True, pady=12)
        tk.Label(rite, text="RITE", bg=SURFACE, fg=GOLD, font=("Georgia", 11)).pack(anchor="w", padx=12, pady=(10, 4))
        self.phase_labels: list = []
        for name, _weight in PHASES:
            label = tk.Label(rite, text=f"ᚲ  {name}", bg=SURFACE, fg=MUTED, font=("Consolas", 10), anchor="w")
            label.pack(fill="x", padx=12, pady=1)
            self.phase_labels.append(label)
        self.forge_button = self._button(rite, "Forge board 1300", self.toggle_forge, primary=True)
        self.forge_button.pack(fill="x", padx=12, pady=(12, 4))
        self._button(rite, "Wake Docker Desktop", self.on_wake).pack(fill="x", padx=12, pady=4)
        self._button(rite, "Purge temp", self.on_purge).pack(fill="x", padx=12, pady=(4, 12))

        term_wrap = tk.Frame(body, bg=SURFACE, highlightbackground=LINE, highlightthickness=1)
        term_wrap.grid(row=0, column=1, sticky="nsew")
        bar = tk.Frame(term_wrap, bg=RAISED)
        bar.pack(fill="x")
        tk.Label(bar, text="● ● ●", bg=RAISED, fg=GOLD, font=("Consolas", 9)).pack(side="left", padx=8, pady=6)
        tk.Label(bar, text="WINDOWS TERMINAL", bg=RAISED, fg=GOLD, font=("Georgia", 9)).pack(side="left")
        tk.Label(bar, text="selene · board 1300", bg=RAISED, fg=MUTED, font=("Consolas", 9)).pack(side="right", padx=8)
        self.console = tk.Text(
            term_wrap,
            bg=SURFACE,
            fg=FG,
            insertbackground=GOLD,
            font=("Consolas", 10),
            relief="flat",
            wrap="word",
            state="disabled",
            padx=8,
            pady=8,
        )
        self.console.pack(fill="both", expand=True)
        for tone, color in (("gold", GOLD), ("muted", MUTED), ("ember", EMBER), ("out", FG)):
            self.console.tag_configure(tone, foreground=color)
        prompt = tk.Frame(term_wrap, bg=SURFACE)
        prompt.pack(fill="x")
        tk.Label(prompt, text="PS C:\\selene>", bg=SURFACE, fg=GOLD, font=("Consolas", 10)).pack(side="left", padx=(8, 4), pady=8)
        self.entry = tk.Entry(prompt, bg=SURFACE, fg=FG, insertbackground=GOLD, relief="flat", font=("Consolas", 10))
        self.entry.pack(side="left", fill="x", expand=True, pady=8)
        self.entry.bind("<Return>", lambda _event: self.on_command())
        self.entry.bind("<Up>", self._history_up)
        self.entry.bind("<Down>", self._history_down)

        footer = tk.Frame(self.root, bg=BG, highlightbackground=LINE, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        tk.Label(footer, text=RUNES, bg=BG, fg=GOLD, font=("Consolas", 8)).pack(fill="x")
        row = tk.Frame(footer, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 4))
        self.bar = tk.Canvas(row, height=10, bg=RAISED, highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True, pady=6)
        self.pct_label = tk.Label(row, text="0%", bg=BG, fg=GOLD, font=("Consolas", 12), width=5, anchor="e")
        self.pct_label.pack(side="right", padx=(8, 0))
        tk.Label(footer, textvariable=self.phase, bg=BG, fg=MUTED, font=("Consolas", 9)).pack(pady=(0, 6))
        self.bar.bind("<Configure>", lambda _event: self._draw_bar())

    def _button(self, parent, text: str, command, primary: bool = False):
        return self.tk.Button(
            parent,
            text=text,
            command=command,
            bg=GOLD if primary else RAISED,
            fg=BG if primary else FG,
            activebackground=GOLD,
            activeforeground=BG,
            relief="flat",
            font=("Consolas", 10),
            pady=8,
            cursor="hand2",
        )

    def write(self, text: str, tone: str = "out") -> None:
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n", tone)
        self.console.see("end")
        self.console.configure(state="disabled")

    def set_progress(self, index: int, fraction: float) -> None:
        value = percent_for(index, fraction)
        self.percent = value
        self.phase.set(PHASES[index][0])
        self.pct_label.configure(text=f"{value}%")
        for i, label in enumerate(self.phase_labels):
            name = PHASES[i][0]
            if value >= 100 or i < index:
                label.configure(text=f"ᛟ  {name}", fg=GOLD)
            elif i == index:
                label.configure(text=f"ᛏ  {name}", fg=FG)
            else:
                label.configure(text=f"ᚲ  {name}", fg=MUTED)
        self._draw_bar()

    def _draw_bar(self) -> None:
        width = max(1, self.bar.winfo_width())
        fill = int(width * (self.percent / 100))
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, width, 10, fill=RAISED, outline="")
        if fill:
            self.bar.create_rectangle(0, 0, fill, 10, fill=GOLD, outline="")

    def choose_source(self) -> None:
        if self.source_kind.get() == "directory":
            chosen = self.filedialog.askdirectory(title="ESP-Miner directory")
        else:
            chosen = self.filedialog.askopenfilename(
                title="ESP-Miner archive",
                filetypes=[("Archives", "*.zip *.tar *.tar.gz *.tgz"), ("All files", "*.*")],
            )
        if not chosen:
            return
        self.source_path = Path(chosen)
        self.path_label.configure(text=str(self.source_path), fg=FG)
        self.write(f"Staged {self.source_path.name}. Temp is unchanged until forge.", "gold")

    def choose_output(self) -> None:
        chosen = self.filedialog.askdirectory(title="Folder for the finished firmware")
        if not chosen:
            return
        path = Path(chosen)
        if inside_temp(path):
            self.write("That folder is inside the temp vault. The next forge would delete it.", "ember")
            return
        self.output_path = path
        save_output(path)
        self.output_label.configure(text=str(path), fg=FG)
        self.write(f"Finished images will be copied to {path}.", "gold")

    def on_purge(self) -> None:
        if self.busy:
            return
        count = wipe_temp()
        self.write(f"Purged {count} objects from {temp_root()}." if count else "Temp is already empty.", "gold")

    def on_wake(self) -> None:
        if self.busy:
            return
        self.write("PS C:\\selene> docker desktop start", "gold")
        threading.Thread(target=self._probe_docker, args=(True,), daemon=True).start()

    def _probe_docker(self, announce: bool = False) -> None:
        ok, detail = wake_docker()
        self.events.put(("docker", ok, detail, announce))

    def toggle_forge(self) -> None:
        if self.busy:
            self.stop.set()
            self.write("^C", "ember")
            return
        if self.source_path is None:
            self.write("Stage a directory or archive first.", "ember")
            return
        self.busy = True
        self.forge_button.configure(text="Stop forge")
        self.stop.clear()
        threading.Thread(target=self._forge, daemon=True).start()

    def _note(self, line: str, n: int, phase: int) -> None:
        self.events.put(("line", line, "out"))
        self.events.put(("progress", phase, min(0.97, n / 80)))

    def _forge(self) -> None:
        source = self.source_path
        assert source is not None
        try:
            self.events.put(("progress", 0, 0.1))
            self.events.put(("line", "PS C:\\selene> docker desktop start", "gold"))
            ok, detail = wake_docker()
            self.events.put(("docker", ok, detail, False))
            self.events.put(("line", detail, "gold" if ok else "ember"))
            self.events.put(("progress", 0, 1.0))
            if self.stop.is_set():
                return

            self.events.put(("progress", 1, 0.2))
            count = wipe_temp()
            self.events.put(("line", f"PS C:\\selene> rmdir /s /q {temp_root()}", "gold"))
            self.events.put(("line", f"Purged {count} objects from temp." if count else "Temp was already empty.", "gold"))
            self.events.put(("progress", 1, 1.0))
            if self.stop.is_set():
                return

            self.events.put(("progress", 2, 0.2))
            dest = work_dir()
            if self.source_kind.get() == "directory":
                self.events.put(("line", f'PS C:\\selene> robocopy "{source}" "{dest}" /e', "gold"))
                written, skipped = copy_directory(source, dest)
            else:
                self.events.put(("line", f'PS C:\\selene> tar -xf "{source}" -C "{dest}"', "gold"))
                written, skipped = extract_archive(source, dest), 0
            self.events.put(("line", f"Staged {written} files into {dest}.", "out"))
            if skipped:
                self.events.put(("line", f"Skipped {skipped} locked files. .git folders are not copied.", "muted"))
            project = locate_project(dest)
            if project != dest:
                self.events.put(("line", f"Project root is {project}.", "gold"))
            else:
                self.events.put(("line", f"Project root is {project}.", "muted"))
            self.events.put(("line", "Target locked: Bitaxe Gamma Hex · board 1300 · 6× BM1370 · ESP32-S3.", "gold"))
            self.events.put(("progress", 2, 1.0))
            if self.stop.is_set():
                return

            self.events.put(("progress", 3, 0.15))
            secp_dir = project / SECP_REL
            self.events.put(("line", f"PS {project}> git clone --depth 1 {SECP_URL}", "gold"))
            if secp_present(project):
                self.events.put(("line", "libsecp256k1 already present. Clone skipped.", "gold"))
            else:
                self.events.put(("line", "libsecp256k1 missing. Cloning bitcoin-core/secp256k1...", "gold"))
                secp_dir.parent.mkdir(parents=True, exist_ok=True)
                if secp_dir.exists():
                    force_remove(secp_dir)
                seen = {"n": 0}

                def on_git(line: str) -> None:
                    seen["n"] += 1
                    self._note(line, seen["n"], 3)

                code = popen_stream(["git", "clone", "--depth", "1", SECP_URL, str(secp_dir)], project, self.stop, on_git)
                if self.stop.is_set():
                    return
                if code != 0 or not secp_present(project):
                    self.events.put(("line", "git clone of libsecp256k1 failed. Install Git for Windows and forge again.", "ember"))
                    self.events.put(("phase", "Halted"))
                    return
                self.events.put(("line", "libsecp256k1 is in the temp tree.", "gold"))
            self.events.put(("progress", 3, 1.0))
            if not ok:
                self.events.put(("line", "Docker Desktop is not running. No factory binary was written.", "ember"))
                self.events.put(("phase", "Engine offline"))
                return

            devcontainer = project / ".devcontainer"
            dockerfile = devcontainer / "Dockerfile"
            if not dockerfile.is_file():
                dockerfile = devcontainer / "dockerfile" if (devcontainer / "dockerfile").is_file() else None
            build_context = devcontainer if dockerfile is not None else None
            if build_context is None and (project / "Dockerfile").is_file():
                build_context = project
            if build_context is None:
                self.events.put((
                    "line",
                    f"No .devcontainer in {project}. Pulling {IDF_FALLBACK} (ESP-Miner CI image) and tagging {IMAGE}.",
                    "gold",
                ))
                image_cmd = ["docker", "pull", IDF_FALLBACK]
            else:
                self.events.put(("line", f"PS {project}> docker build -t {IMAGE} {build_context}", "gold"))
                image_cmd = ["docker", "build", "-t", IMAGE, str(build_context)]
            seen = {"n": 0}

            def on_image(line: str) -> None:
                seen["n"] += 1
                self._note(line, seen["n"], 4)

            code = popen_stream(image_cmd, project, self.stop, on_image)
            if code == 0 and build_context is None:
                code = popen_stream(["docker", "tag", IDF_FALLBACK, IMAGE], project, self.stop, on_image)
            self.events.put(("progress", 4, 1.0))
            if self.stop.is_set():
                return
            if code != 0:
                self.events.put(("line", f"docker image step exited {code}.", "ember"))
                self.events.put(("phase", "Halted"))
                return

            mount = str(project.resolve())
            record = find_board_record(project)
            if record is None:
                self.events.put((
                    "line",
                    "No Gamma Hex board record. Expected configs/config-1300.csv or config-1300.cvs. Without it the ST7789 stays black.",
                    "ember",
                ))
                self.events.put(("phase", "Halted"))
                return
            build_cmd = idf_build_command(record)
            self.events.put(("line", f"Board 1300 NVS from {record.as_posix()}: GammaHex, ST7789 color pins.", "gold"))
            self.events.put(
                ("line", f'PS {project}> docker run --rm -v "{mount}:/workspace" -w /workspace {IMAGE} bash -lc "{build_cmd}"', "gold"),
            )
            seen = {"n": 0}

            def on_idf(line: str) -> None:
                seen["n"] += 1
                self._note(line, seen["n"], 5)

            code = popen_stream(
                ["docker", "run", "--rm", "-v", f"{mount}:/workspace", "-w", "/workspace", IMAGE, "bash", "-lc", build_cmd],
                project,
                self.stop,
                on_idf,
            )
            self.events.put(("progress", 5, 1.0))
            if self.stop.is_set():
                return
            if code != 0:
                self.events.put(("line", f"idf.py build exited {code}.", "ember"))
                self.events.put(("phase", "Halted"))
                return
            factory = project / FACTORY
            self.events.put(("progress", 6, 1.0))
            copied = deliver_build(project, self.output_path)
            if self.output_path is None:
                self.events.put(("line", "No output folder selected. The image stays in the build tree.", "ember"))
                if factory.is_file():
                    self.events.put(("line", f"Factory image written: {factory}", "gold"))
                self.events.put(("phase", "Build finished"))
            elif copied:
                for path in copied:
                    self.events.put(("line", f"Copied {path.name} to {path}", "gold"))
                self.events.put((
                    "line",
                    "Flash the factory image, not esp-miner.bin alone. It writes the 1300 board record that turns the color screen on.",
                    "gold",
                ))
                self.events.put(("phase", "Factory image ready"))
            else:
                self.events.put(("line", f"Build finished, but no factory binary was found in {project}.", "ember"))
                self.events.put(("phase", "Halted"))
        except Exception as exc:
            self.events.put(("line", str(exc), "ember"))
            self.events.put(("phase", "Halted"))
        finally:
            self.events.put(("idle",))

    def on_command(self) -> None:
        text = self.entry.get().strip()
        self.entry.delete(0, "end")
        if not text:
            return
        self.history.append(text)
        self.history_at = len(self.history)
        self.write(f"PS C:\\selene> {text}", "gold")
        if self.busy and text.lower() not in {"stop", "^c"}:
            self.write("The forge is holding the console. Type stop.", "muted")
            return
        head, _, rest = text.partition(" ")
        command = head.lower()
        if command in {"stop", "^c"}:
            self.stop.set()
            return
        if command == "help":
            for line in (
                "forge            build Gamma Hex board 1300",
                "Choose output    copies the finished bin out of temp",
                "docker ...       passed to the Docker CLI",
                "secp             libsecp256k1 in the temp tree",
                "board            Gamma Hex 1300 target",
                "dir              temp tree",
                "purge            delete the selene temp directory",
                "cls              clear the console",
                "any other line   runs in cmd.exe inside the temp tree",
            ):
                self.write(line, "out")
            return
        if command in {"cls", "clear"}:
            self.console.configure(state="normal")
            self.console.delete("1.0", "end")
            self.console.configure(state="disabled")
            return
        if command == "board":
            for line in (
                "Board version   1300",
                "Family          Gamma Hex",
                "ASIC            6× BM1370, two domains of three",
                "Controller      ESP32-S3",
                "Factory image   esp-miner-factory-1300.bin",
            ):
                self.write(line, "gold")
            return
        if command == "secp":
            root = work_dir()
            if secp_present(root):
                self.write(f"libsecp256k1 present in {root / SECP_REL}", "gold")
            else:
                self.write("libsecp256k1 is not in the temp tree. Forge will clone it.", "ember")
            return
        if command in {"dir", "ls"}:
            root = work_dir()
            shown = 0
            if root.exists():
                for path in root.rglob("*"):
                    if not path.is_file():
                        continue
                    if shown >= 40:
                        self.write("...", "muted")
                        break
                    self.write(str(path.relative_to(root)), "out")
                    shown += 1
            if shown == 0:
                self.write("Temp is empty.", "muted")
            return
        if command == "purge":
            self.on_purge()
            return
        if command in {"forge", "build"}:
            self.toggle_forge()
            return
        if command == "docker":
            args = ["docker", *rest.split()] if rest else ["docker", "version"]
            threading.Thread(target=self._run_user, args=(args, work_dir()), daemon=True).start()
            return
        shell = ["cmd.exe", "/d", "/c", text] if WIN else ["bash", "-lc", text]
        cwd = work_dir() if work_dir().exists() else Path.cwd()
        threading.Thread(target=self._run_user, args=(shell, cwd), daemon=True).start()

    def _run_user(self, args: list[str], cwd: Path) -> None:
        code = popen_stream(args, cwd, threading.Event(), lambda line: self.events.put(("line", line, "out")))
        if code not in (0, None):
            self.events.put(("line", f"exit {code}", "ember"))

    def _history_up(self, _event):
        if not self.history:
            return "break"
        self.history_at = max(0, self.history_at - 1)
        self.entry.delete(0, "end")
        self.entry.insert(0, self.history[self.history_at])
        return "break"

    def _history_down(self, _event):
        if not self.history:
            return "break"
        self.history_at = min(len(self.history), self.history_at + 1)
        self.entry.delete(0, "end")
        if self.history_at < len(self.history):
            self.entry.insert(0, self.history[self.history_at])
        return "break"

    def _pump(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "line":
                    self.write(event[1], event[2])
                elif kind == "progress":
                    self.set_progress(event[1], event[2])
                elif kind == "phase":
                    self.phase.set(event[1])
                elif kind == "docker":
                    running, detail, announce = event[1], event[2], event[3]
                    self.docker_text.set("Running" if running else "Offline")
                    if announce:
                        self.write(detail, "gold" if running else "ember")
                elif kind == "idle":
                    self.busy = False
                    self.forge_button.configure(text="Forge board 1300")
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    try:
        ForgeApp().run()
    except Exception as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
