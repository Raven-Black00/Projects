#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Selene Forge GUI v3.5 - Bitaxe Gamma HEX 1300 (6x ASIC) firmware forge.
Black & gold Viking theme. Docker-based ESP-IDF build. Needs Python 3.9+ with tkinter, plus Docker and Git."""
import os, re, sys, json, math, stat, time, shutil, struct, queue, tarfile, zipfile, tempfile, threading, subprocess, platform
import urllib.request

VERSION = "3.5"
DEFAULT_IMAGE = "espressif/idf:v6.0.3"
SECP_URL = "https://github.com/bitcoin-core/secp256k1"
ESPMINER_REPO = "bitaxeorg/ESP-Miner"
KNOWN_SECP_PINS = {"2.15.4": "0cdc758a56360bf58a851fe91085a327ec97685a"}
WRAPPER_CMAKE = """idf_component_register(
    SRCS "libsecp256k1/src/secp256k1.c"
         "libsecp256k1/src/precomputed_ecmult.c"
         "libsecp256k1/src/precomputed_ecmult_gen.c"
    INCLUDE_DIRS "libsecp256k1/include"
    PRIV_INCLUDE_DIRS "libsecp256k1" "libsecp256k1/src"
)
target_compile_definitions(${COMPONENT_LIB} PRIVATE
    ECMULT_WINDOW_SIZE=4
    ECMULT_GEN_PREC_BITS=2
    ENABLE_MODULE_ELLSWIFT=1
    ENABLE_MODULE_SCHNORRSIG=1
    ENABLE_MODULE_EXTRAKEYS=1
)
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-unused-parameter -Wno-unused-function)
"""
IS_WIN = os.name == "nt"
SETTINGS = os.path.join(os.path.expanduser("~"), ".seleneforge.json")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NINJA = re.compile(r"\[(\d+)/(\d+)\]")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
ARCHIVE_EXT = (".zip", ".rar", ".7z", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
SKIP_DIRS = {".git", "node_modules", "build", "__pycache__", "appdata", "windows", "$recycle.bin", "system volume information",
             "library", "proc", "sys", "dev", "snap", ".cache", ".npm", "winsxs"}
TOOLS = {"docker": "docker", "git": "git", "7z": ""}
PROG_NAMES = {"docker": {"docker", "docker.exe"}, "git": {"git", "git.exe"}, "7z": {"7z", "7z.exe", "7zz", "7zz.exe", "7za", "7za.exe"}}

GOLD, GOLD_D, GOLD_L = "#d4af37", "#8a6d1a", "#f5e08a"
BLACK, PANEL, INK, DIM = "#050505", "#111111", "#e8d9a8", "#8c7a45"

STAGES = [
    ("Prepare & validate", 0, 5), ("Submodules + secp256k1", 5, 10), ("Docker image", 10, 20),
    ("Forge firmware", 20, 80), ("Web partition", 80, 86), ("Factory image", 86, 96), ("Export", 96, 100),
]


# ------------------------------------------------------------------ logic (GUI-free)
def popen_flags():
    return {"creationflags": 0x08000000} if IS_WIN else {}


def tool_cmd(cmd):
    if cmd and cmd[0] in TOOLS and TOOLS[cmd[0]]:
        return [TOOLS[cmd[0]]] + list(cmd[1:])
    return list(cmd)


def tool_ok(name):
    p = TOOLS.get(name, "")
    return bool(p) and (os.path.isfile(p) or shutil.which(p) is not None)


def srun(cmd, **kw):
    return subprocess.run(tool_cmd(cmd), **kw)


def rmtree_force(path):
    """Delete a folder tree even with read-only files. ONLY used on temp folders this app created."""
    def onerr(func, p, exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if os.path.isdir(path):
        shutil.rmtree(path, onerror=onerr)


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "SeleneForge/" + VERSION, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def pinned_secp_commit(ver):
    """Commit of libsecp256k1 that this exact ESP-Miner release pins. GitHub first, built-in table as fallback."""
    if re.match(r"^\d+\.\d+\.\d+$", ver or ""):
        try:
            data = json.loads(http_get("https://api.github.com/repos/%s/contents/components/libsecp256k1/libsecp256k1?ref=v%s" % (ESPMINER_REPO, ver)))
            if isinstance(data, dict) and SHA40.match(data.get("sha", "")):
                return data["sha"], "GitHub tag v" + ver
        except Exception:
            pass
        if ver in KNOWN_SECP_PINS:
            return KNOWN_SECP_PINS[ver], "built-in table"
    return None, "unknown"


def official_wrapper_cmake(ver):
    if re.match(r"^\d+\.\d+\.\d+$", ver or ""):
        try:
            txt = http_get("https://raw.githubusercontent.com/%s/v%s/components/libsecp256k1/CMakeLists.txt" % (ESPMINER_REPO, ver))
            if "idf_component_register" in txt:
                return txt, "GitHub tag v" + ver
        except Exception:
            pass
    return WRAPPER_CMAKE, "built-in copy"


def is_firmware_root(p):
    return (os.path.isfile(os.path.join(p, "CMakeLists.txt")) and os.path.isdir(os.path.join(p, "main"))
            and (os.path.isfile(os.path.join(p, "partitions.csv")) or os.path.isfile(os.path.join(p, "sdkconfig.defaults"))
                 or os.path.isfile(os.path.join(p, "sdkconfig"))))


def find_firmware_root(base, depth=4):
    base = os.path.abspath(base)
    if is_firmware_root(base):
        return base
    if depth <= 0:
        return None
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for n in names:
        if n in (".git", "node_modules", "build"):
            continue
        sub = os.path.join(base, n)
        if os.path.isdir(sub):
            r = find_firmware_root(sub, depth - 1)
            if r:
                return r
    return None


def extract_archive(archive, dest, log):
    low = archive.lower()
    if low.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return True
    if low.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive) as t:
            t.extractall(dest)
        return True
    tools = []
    if TOOLS.get("7z"):
        tools.append([TOOLS["7z"], "x", "-y", "-o" + dest, archive])
    tools += [["7z", "x", "-y", "-o" + dest, archive], ["7zz", "x", "-y", "-o" + dest, archive], ["7za", "x", "-y", "-o" + dest, archive],
              ["bsdtar", "-xf", archive, "-C", dest], ["tar", "-xf", archive, "-C", dest], ["unrar", "x", "-y", archive, dest + os.sep]]
    for cmd in tools:
        if shutil.which(cmd[0]) or os.path.isfile(cmd[0]):
            log("extracting with %s" % cmd[0])
            r = subprocess.run(cmd, capture_output=True, text=True, **popen_flags())
            if r.returncode == 0:
                return True
    return False


def parse_partition_table(path):
    out = []
    with open(path, "rb") as f:
        data = f.read()
    for i in range(0, len(data) - 31, 32):
        e = data[i:i + 32]
        if e[:2] != b"\xaa\x50":
            break
        ptype, sub, off, size = struct.unpack("<BBII", e[2:12])
        name = e[12:28].split(b"\0")[0].decode("ascii", "replace")
        out.append({"name": name, "type": ptype, "subtype": sub, "offset": off, "size": size})
    return out


def pick_idf_image(root):
    best = (6, 0, 3)
    try:
        for dp, _, fs in os.walk(os.path.join(root, "main")):
            for fn in fs:
                if fn == "idf_component.yml":
                    txt = open(os.path.join(dp, fn), encoding="utf-8", errors="ignore").read()
                    m = re.search(r"^\s*idf:\s*['\"]?[<>=~^!\s]*(\d+)\.(\d+)(?:\.(\d+))?", txt, re.M)
                    if m:
                        v = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
                        if v > best:
                            best = v
    except OSError:
        pass
    return "espressif/idf:v%d.%d.%d" % best


def read_version(root):
    p = os.path.join(root, "version.txt")
    if os.path.isfile(p):
        v = open(p, encoding="utf-8", errors="ignore").read().strip().lstrip("v")
        if v:
            return v
    if os.path.isdir(os.path.join(root, ".git")):
        try:
            r = srun(["git", "describe", "--tags", "--always"], cwd=root, capture_output=True, text=True, **popen_flags())
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().lstrip("v")
        except OSError:
            pass
    m = re.search(r"v?(\d+\.\d+\.\d+)", os.path.basename(os.path.normpath(root)))
    return m.group(1) if m else "dev"


def ver_tuple(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def parse_gitmodules(root):
    p = os.path.join(root, ".gitmodules")
    mods, cur = [], None
    if os.path.isfile(p):
        for line in open(p, encoding="utf-8", errors="ignore"):
            line = line.strip()
            if line.startswith("[submodule"):
                cur = {}
                mods.append(cur)
            elif "=" in line and cur is not None:
                k, v = [s.strip() for s in line.split("=", 1)]
                cur[k] = v
    return [m for m in mods if "path" in m and "url" in m]


def find_web_dist(root):
    base = os.path.join(root, "main", "http_server", "axe-os")
    for dp, _, fs in os.walk(base):
        if "node_modules" in dp:
            continue
        if "index.html" in fs and os.sep + "dist" in dp:
            return dp
    return None


def secp_ok(d):
    return os.path.isfile(os.path.join(d, "src", "secp256k1.c")) or os.path.isfile(os.path.join(d, "include", "secp256k1.h"))


def secp_targets(root, mods):
    t = [os.path.normpath(os.path.join(root, m["path"])) for m in mods if "secp256k1" in (m["path"] + m["url"]).lower()]
    if t:
        return list(dict.fromkeys(t))
    comp = os.path.join(root, "components")
    if os.path.isdir(comp):
        for d in sorted(os.listdir(comp)):
            p = os.path.join(comp, d)
            if "secp256k1" not in d.lower() or not os.path.isdir(p):
                continue
            cm = os.path.join(p, "CMakeLists.txt")
            if os.path.isfile(cm):
                try:
                    txt = open(cm, encoding="utf-8", errors="ignore").read()
                except OSError:
                    txt = ""
                if secp_ok(p):
                    return [p]
                sub = os.path.join(p, "libsecp256k1")
                if "libsecp256k1" in txt or os.path.isdir(sub):
                    return [sub]
                return [os.path.join(p, "secp256k1")]
            if os.path.isdir(os.path.join(p, "libsecp256k1")):
                return [os.path.join(p, "libsecp256k1")]
            return [p]
    return [os.path.join(comp, "libsecp256k1", "libsecp256k1")]


def wrapper_component_dir(dest):
    """For components/libsecp256k1/libsecp256k1 return components/libsecp256k1 (the ESP-IDF component folder), else None."""
    parent = os.path.dirname(dest)
    if os.path.basename(dest).lower() == "libsecp256k1" and os.path.basename(parent).lower() == "libsecp256k1" \
            and os.path.basename(os.path.dirname(parent)).lower() == "components":
        return parent
    return None


# ---- search helpers (one per input line)
def search_roots():
    home = os.path.expanduser("~")
    roots = [os.path.join(home, n) for n in ("Desktop", "Downloads", "Documents", "Projects", "PROJECTS", "source", "repos", "Dropbox")]
    roots = [r for r in roots if os.path.isdir(r)]
    if IS_WIN:
        for dl in "CDEFG":
            for n in ("\\Projects", "\\esp-miner", "\\ESP-Miner"):
                p = dl + ":" + n
                if os.path.isdir(p) and p not in roots:
                    roots.append(p)
    else:
        roots.append(home)
    return list(dict.fromkeys(roots))


def program_roots():
    if IS_WIN:
        env = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
        la = os.environ.get("LOCALAPPDATA")
        env += [os.path.join(la, "Programs") if la else None]
        return [p for p in dict.fromkeys(env) if p and os.path.isdir(p)]
    return [p for p in ("/usr/bin", "/usr/local/bin", "/opt", "/Applications", "/snap/bin", "/opt/homebrew/bin") if os.path.isdir(p)]


def known_program_paths(kind):
    pf = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    la = os.environ.get("LOCALAPPDATA", "")
    if kind == "docker":
        c = [os.path.join(p, r"Docker\Docker\resources\bin\docker.exe") for p in pf]
        c += ["/usr/bin/docker", "/usr/local/bin/docker", "/Applications/Docker.app/Contents/Resources/bin/docker"]
    elif kind == "git":
        c = [os.path.join(p, r"Git\cmd\git.exe") for p in pf] + [os.path.join(p, r"Git\bin\git.exe") for p in pf]
        c += [os.path.join(la, r"Programs\Git\cmd\git.exe"), "/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git"]
    else:
        c = [os.path.join(p, r"7-Zip\7z.exe") for p in pf] + ["/usr/bin/7z", "/usr/bin/7zz", "/usr/local/bin/7zz", "/opt/homebrew/bin/7zz"]
    return c


def walk_limited(roots, max_depth=5, deadline=20.0, cancel=None):
    end = time.time() + deadline
    for base in roots:
        base_depth = base.rstrip("\\/").count(os.sep)
        for dp, dns, fns in os.walk(base):
            if time.time() > end or (cancel and cancel()):
                return
            if dp.count(os.sep) - base_depth >= max_depth:
                dns[:] = []
            dns[:] = [d for d in dns if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            yield dp, dns, fns


def search_items(kind, cancel=None):
    res, seen = [], set()

    def add(label, val):
        key = os.path.normcase(val)
        if val and key not in seen:
            seen.add(key)
            res.append((label, val))

    if kind in PROG_NAMES:
        for n in sorted(PROG_NAMES[kind]):
            w = shutil.which(n)
            if w:
                add("[PATH]   " + w, w)
        for p in known_program_paths(kind):
            if os.path.isfile(p):
                add("[known]  " + p, p)
        for dp, dns, fns in walk_limited(program_roots(), 4, 12, cancel):
            for f in fns:
                if f.lower() in PROG_NAMES[kind]:
                    add("[found]  " + os.path.join(dp, f), os.path.join(dp, f))
        return res
    if kind == "img":
        if tool_ok("docker"):
            try:
                r = srun(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True, **popen_flags())
                for line in r.stdout.splitlines():
                    if line.startswith(("espressif/idf", "seleneforge-")) and "<none>" not in line:
                        add("[local] " + line, line)
            except OSError:
                pass
        for t in ("espressif/idf:v6.0.3", "espressif/idf:v6.0.2", "espressif/idf:v5.5.1", "espressif/idf:v5.4.2"):
            add("[hub]   " + t, t)
        return res
    if kind == "out":
        home = os.path.expanduser("~")
        for n in ("Desktop", "Downloads", "Documents"):
            p = os.path.join(home, n)
            if os.path.isdir(p):
                add("[folder] " + os.path.join(p, "SeleneForge_out"), os.path.join(p, "SeleneForge_out"))
        for dp, dns, _ in walk_limited(search_roots(), 4, 10, cancel):
            for d in dns:
                if "seleneforge" in d.lower():
                    add("[found]  " + os.path.join(dp, d), os.path.join(dp, d))
        return res
    for dp, dns, fns in walk_limited(search_roots(), 5, 20, cancel):
        if kind == "src":
            if is_firmware_root(dp):
                add("[folder]  " + dp, dp)
                dns[:] = []
                continue
            for f in fns:
                lf = f.lower()
                if lf.endswith(ARCHIVE_EXT) and re.search(r"esp.?miner|bitaxe|axe|hex", lf):
                    add("[archive] " + os.path.join(dp, f), os.path.join(dp, f))
        elif kind == "cfg":
            for f in fns:
                lf = f.lower()
                if lf.endswith((".csv", ".cvs")) and "config" in lf:
                    add(os.path.join(dp, f), os.path.join(dp, f))
        if len(res) >= 80:
            break
    return res


# ------------------------------------------------------------------ GUI
def run_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    root = tk.Tk()
    root.title("Selene Forge GUI v%s - Bitaxe Gamma HEX 1300" % VERSION)
    root.geometry("1160x900")
    root.configure(bg=BLACK)
    q = queue.Queue()

    st = {"proc": None, "container": None, "cancel": False, "tmp": None, "thread": None,
          "pause": threading.Event(), "target": 0.0, "shown": 0.0, "phase": 0.0, "stage": -1, "max_pct": 0.0}
    st["pause"].set()

    rune_font = ("Segoe UI Historic", 20) if IS_WIN else ("Noto Sans Runic", 20)
    mono = ("Consolas", 10) if IS_WIN else ("DejaVu Sans Mono", 10)

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=GOLD, arrowcolor=GOLD, bordercolor=GOLD_D)
    style.configure("Vertical.TScrollbar", background=GOLD_D, troughcolor=BLACK, arrowcolor=GOLD, bordercolor=BLACK)

    def mkbtn(parent, text, cmd, w=14):
        b = tk.Button(parent, text=text, command=cmd, width=w, bg=PANEL, fg=GOLD, activebackground=GOLD, activeforeground=BLACK,
                      relief="flat", bd=0, highlightthickness=1, highlightbackground=GOLD_D, highlightcolor=GOLD,
                      font=("Georgia", 10, "bold"), cursor="hand2", disabledforeground=DIM)
        b.bind("<Enter>", lambda e: b["state"] == "normal" and b.config(bg=GOLD_D, fg=GOLD_L))
        b.bind("<Leave>", lambda e: b.config(bg=PANEL, fg=GOLD))
        return b

    def mkentry(parent, var):
        return tk.Entry(parent, textvariable=var, bg=PANEL, fg=INK, insertbackground=GOLD, relief="flat", bd=0,
                        highlightthickness=1, highlightbackground=GOLD_D, highlightcolor=GOLD, font=mono)

    def lbl(parent, text):
        return tk.Label(parent, text=text, bg=BLACK, fg=GOLD, font=("Georgia", 10, "bold"))

    head = tk.Frame(root, bg=BLACK)
    head.pack(fill="x", padx=18, pady=(12, 2))
    tk.Label(head, text="\u16cb\u16d6\u16da\u16d6\u16be\u16d6  \u16a0\u16df\u16b1\u16b7\u16d6", bg=BLACK, fg=GOLD, font=rune_font).pack()
    tk.Label(head, text="SELENE FORGE", bg=BLACK, fg=GOLD_L, font=("Georgia", 24, "bold")).pack()
    tk.Label(head, text="Bitaxe Gamma HEX 1300  \u2022  6 ASIC  \u2022  v%s" % VERSION, bg=BLACK, fg=DIM, font=("Georgia", 10, "italic")).pack()
    tk.Frame(root, bg=GOLD_D, height=2).pack(fill="x", padx=18, pady=6)

    cfgd = {}
    try:
        cfgd = json.load(open(SETTINGS, encoding="utf-8"))
    except Exception:
        pass
    v_src = tk.StringVar(value="")
    v_cfg = tk.StringVar(value=cfgd.get("cfg", ""))
    v_out = tk.StringVar(value=cfgd.get("out", os.path.join(os.path.expanduser("~"), "Desktop", "SeleneForge_out")))
    v_img = tk.StringVar(value=DEFAULT_IMAGE)
    v_auto = tk.BooleanVar(value=True)
    v_docker = tk.StringVar(value=cfgd.get("docker") or shutil.which("docker") or "")
    v_git = tk.StringVar(value=cfgd.get("git") or shutil.which("git") or "")
    v_7z = tk.StringVar(value=cfgd.get("7z") or shutil.which("7z") or shutil.which("7zz") or shutil.which("7za") or "")
    st["origin"] = cfgd.get("src", "")
    v_src.set(st["origin"])

    form = tk.Frame(root, bg=BLACK)
    form.pack(fill="x", padx=18)
    form.columnconfigure(1, weight=1)

    def sync_tools():
        TOOLS["docker"] = v_docker.get().strip().strip('"') or "docker"
        TOOLS["git"] = v_git.get().strip().strip('"') or "git"
        TOOLS["7z"] = v_7z.get().strip().strip('"')

    def save_settings():
        try:
            json.dump({"src": st.get("origin") or v_src.get(), "cfg": v_cfg.get(), "out": v_out.get(), "docker": v_docker.get(),
                       "git": v_git.get(), "7z": v_7z.get()}, open(SETTINGS, "w", encoding="utf-8"))
        except OSError:
            pass

    def search_dialog(kind, title, var, on_pick=None):
        sync_tools()
        win = tk.Toplevel(root)
        win.title(title)
        win.configure(bg=BLACK)
        win.geometry("860x420")
        win.transient(root)
        tk.Label(win, text="\u16cb " + title, bg=BLACK, fg=GOLD_L, font=("Georgia", 13, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        state = tk.StringVar(value="Searching...")
        tk.Label(win, textvariable=state, bg=BLACK, fg=DIM, font=("Georgia", 9, "italic")).pack(anchor="w", padx=12)
        fr = tk.Frame(win, bg=GOLD_D, bd=1)
        fr.pack(fill="both", expand=True, padx=12, pady=8)
        lb = tk.Listbox(fr, bg="#030303", fg=INK, selectbackground=GOLD_D, selectforeground=GOLD_L, font=mono, relief="flat", bd=0, activestyle="none")
        sb2 = ttk.Scrollbar(fr, command=lb.yview)
        lb.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        lb.pack(fill="both", expand=True)
        items, rq, stop = [], queue.Queue(), {"v": False}

        def choose(_=None):
            sel = lb.curselection()
            if not sel or not items[sel[0]][1]:
                return
            val = items[sel[0]][1]
            var.set(val)
            if on_pick:
                on_pick(val)
            sync_tools()
            stop["v"] = True
            win.destroy()

        def poll():
            try:
                res = rq.get_nowait()
            except queue.Empty:
                if not stop["v"]:
                    win.after(150, poll)
                return
            items.extend(res)
            for lab, _ in res:
                lb.insert("end", lab)
            state.set("%d result(s). Double-click or press Select." % len(res) if res else "Nothing found. Use Browse to locate it by hand.")
            if res:
                lb.selection_set(0)

        def work():
            try:
                rq.put(search_items(kind, lambda: stop["v"]))
            except Exception as e:
                rq.put([("error: %s" % e, "")])

        def close():
            stop["v"] = True
            win.destroy()

        bb = tk.Frame(win, bg=BLACK)
        bb.pack(fill="x", padx=12, pady=(0, 10))
        mkbtn(bb, "Select", choose, 10).pack(side="left", padx=3)
        mkbtn(bb, "Close", close, 10).pack(side="left", padx=3)
        lb.bind("<Double-Button-1>", choose)
        win.protocol("WM_DELETE_WINDOW", close)
        threading.Thread(target=work, daemon=True).start()
        poll()

    def add_row(r, text, var, browse_cmd, kind, title, browse2=None, on_pick=None):
        lbl(form, text).grid(row=r, column=0, sticky="w", pady=3)
        mkentry(form, var).grid(row=r, column=1, sticky="ew", padx=8, pady=3, ipady=3)
        f = tk.Frame(form, bg=BLACK)
        f.grid(row=r, column=2, sticky="e")
        if browse2:
            mkbtn(f, browse2[0], browse2[1], 8).pack(side="left", padx=2)
        mkbtn(f, "Browse", browse_cmd, 8).pack(side="left", padx=2)
        mkbtn(f, "Search", lambda: search_dialog(kind, title, var, on_pick), 8).pack(side="left", padx=2)

    def browse_dir(var, title):
        def go():
            d = filedialog.askdirectory(title=title)
            if d:
                var.set(d)
        return go

    def browse_file(var, title, types):
        def go():
            f = filedialog.askopenfilename(title=title, filetypes=types)
            if f:
                var.set(f)
        return go

    def src_picked(val):
        st["origin"] = val

    exe_types = [("Programs", "*.exe" if IS_WIN else "*"), ("All", "*.*")]
    add_row(0, "ESP-Miner source", v_src, lambda: (browse_dir(v_src, "Select ESP-Miner folder")(), st.update(origin=v_src.get())),
            "src", "Find ESP-Miner source folder/archive", on_pick=src_picked,
            browse2=("Archive", lambda: (browse_file(v_src, "Select archive", [("Archives", "*.zip *.rar *.7z *.tar *.tar.gz *.tgz *.tar.bz2 *.tar.xz"), ("All", "*.*")])(), st.update(origin=v_src.get()))))
    add_row(1, "config.csv (optional)", v_cfg, browse_file(v_cfg, "Select config.csv", [("CSV", "*.csv *.cvs"), ("All", "*.*")]), "cfg", "Find config.csv")
    add_row(2, "Output folder", v_out, browse_dir(v_out, "Output folder"), "out", "Find output folder")

    lbl(form, "Docker image").grid(row=3, column=0, sticky="w", pady=3)
    img_cb = ttk.Combobox(form, textvariable=v_img, values=["espressif/idf:v6.0.3", "espressif/idf:v6.0.2", "espressif/idf:v5.5.1", "espressif/idf:v5.4.2"])
    img_cb.grid(row=3, column=1, sticky="ew", padx=8, pady=3)
    f3 = tk.Frame(form, bg=BLACK)
    f3.grid(row=3, column=2, sticky="e")
    tk.Checkbutton(f3, text="Auto-pick", variable=v_auto, bg=BLACK, fg=INK, selectcolor=PANEL,
                   activebackground=BLACK, activeforeground=GOLD, font=("Georgia", 9)).pack(side="left", padx=2)
    mkbtn(f3, "Search", lambda: search_dialog("img", "Find Docker image", v_img, lambda v: v_auto.set(False)), 8).pack(side="left", padx=2)

    add_row(4, "Docker program", v_docker, browse_file(v_docker, "Select docker executable", exe_types), "docker", "Find Docker program (docker.exe)")
    add_row(5, "Git program", v_git, browse_file(v_git, "Select git executable", exe_types), "git", "Find Git program (git.exe)")
    add_row(6, "7-Zip (for .rar/.7z)", v_7z, browse_file(v_7z, "Select 7z executable", exe_types), "7z", "Find 7-Zip program (7z.exe)")

    sf = tk.Frame(root, bg=BLACK)
    sf.pack(fill="x", padx=18, pady=(10, 2))
    stage_lbls = []
    for i, (n, _, _) in enumerate(STAGES):
        l = tk.Label(sf, text="\u16df " + n, bg=BLACK, fg=DIM, font=("Georgia", 9))
        l.grid(row=0, column=i, sticky="w", padx=6)
        sf.columnconfigure(i, weight=1)
        stage_lbls.append(l)

    bar = tk.Canvas(root, height=30, bg=PANEL, highlightthickness=1, highlightbackground=GOLD_D)
    bar.pack(fill="x", padx=18, pady=6)
    status_var = tk.StringVar(value="The forge is cold. Choose your source.")
    tk.Label(root, textvariable=status_var, bg=BLACK, fg=INK, font=("Georgia", 10, "italic")).pack(anchor="w", padx=20)

    def lerp(c1, c2, t):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

    def draw_bar():
        w = max(bar.winfo_width(), 10)
        h = bar.winfo_height()
        bar.delete("all")
        st["shown"] += (st["target"] - st["shown"]) * 0.12
        if abs(st["target"] - st["shown"]) < 0.05:
            st["shown"] = st["target"]
        fw = int(w * st["shown"] / 100.0)
        st["phase"] = (st["phase"] + 0.035) % 1.0
        for x in range(0, fw, 4):
            t = (x / float(w)) * 2.0 - st["phase"] * 2.0
            s = (1 + math.sin(t * math.pi * 2)) / 2.0
            bar.create_rectangle(x, 0, min(x + 4, fw), h, fill=lerp(GOLD_D, GOLD_L, s), width=0)
        bar.create_text(w // 2, h // 2, text="%d%%" % round(st["shown"]), fill=BLACK if st["shown"] > 52 else GOLD, font=("Georgia", 11, "bold"))
        root.after(40, draw_bar)

    bf = tk.Frame(root, bg=BLACK)
    bf.pack(fill="x", padx=18, pady=4)
    b_valid = mkbtn(bf, "Validate", lambda: do_validate(), 10)
    b_sub = mkbtn(bf, "Fetch submodules", lambda: start("sub", "Submodule fetch"), 16)
    b_secp = mkbtn(bf, "Repair secp256k1", lambda: start("secp", "libsecp256k1 repair"), 16)
    b_start = mkbtn(bf, "Forge", lambda: start("full", "Forge"), 10)
    b_pause = mkbtn(bf, "Pause", lambda: toggle_pause(), 9)
    b_cancel = mkbtn(bf, "Cancel", lambda: cancel(), 9)
    b_copy = mkbtn(bf, "Copy log", lambda: copy_log(), 9)
    b_open = mkbtn(bf, "Open output", lambda: open_out(), 11)
    b_clean = mkbtn(bf, "Clean temp", lambda: clean_tmp(True), 10)
    for b in (b_valid, b_sub, b_secp, b_start, b_pause, b_cancel, b_copy, b_open, b_clean):
        b.pack(side="left", padx=2)
    b_pause.config(state="disabled")
    b_cancel.config(state="disabled")

    cf = tk.Frame(root, bg=GOLD_D, bd=1)
    cf.pack(fill="both", expand=True, padx=18, pady=(6, 14))
    console = tk.Text(cf, bg="#030303", fg=INK, insertbackground=GOLD, font=mono, wrap="word", relief="flat", bd=0, state="disabled", height=8)
    sb = ttk.Scrollbar(cf, command=console.yview)
    console.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    console.pack(fill="both", expand=True)
    for tag, col in (("gold", GOLD), ("ok", "#7fd17f"), ("err", "#ff6b5b"), ("warn", "#e6b422"), ("dim", DIM), ("rune", GOLD_L)):
        console.tag_configure(tag, foreground=col)

    def log(msg, tag=None):
        q.put(("log", msg, tag))

    def pump():
        try:
            while True:
                item = q.get_nowait()
                k = item[0]
                if k == "log":
                    console.config(state="normal")
                    console.insert("end", item[1] + "\n", item[2] or ())
                    console.see("end")
                    console.config(state="disabled")
                elif k == "pct":
                    st["max_pct"] = max(st["max_pct"], item[1])
                    st["target"] = st["max_pct"]
                elif k == "stage":
                    st["stage"] = item[1]
                    for i, l in enumerate(stage_lbls):
                        l.config(fg=GOLD_L if i == item[1] else (GOLD if i < item[1] else DIM))
                elif k == "status":
                    status_var.set(item[1])
                elif k == "done":
                    finish(item[1])
        except queue.Empty:
            pass
        root.after(80, pump)

    def notify(msg, tag="ok"):
        log("\u16df " + msg, tag)
        q.put(("status", msg))
        if IS_WIN:
            try:
                import winsound
                winsound.MessageBeep()
            except Exception:
                pass

    def set_pct(p):
        q.put(("pct", p))

    def enter_stage(i):
        q.put(("stage", i))
        set_pct(STAGES[i][1])
        notify("Stage %d/%d: %s" % (i + 1, len(STAGES), STAGES[i][0]), "gold")

    def run_cmd(cmd, cwd=None, stage=None, stdin_text=None, show=True):
        cmd = tool_cmd(cmd)
        if show:
            log("$ " + " ".join(cmd), "dim")
        try:
            p = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE if stdin_text else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding="utf-8", errors="replace", **popen_flags())
        except OSError as e:
            log("Cannot run %s: %s" % (cmd[0], e), "err")
            return 127
        st["proc"] = p
        if stdin_text:
            try:
                p.stdin.write(stdin_text)
                p.stdin.close()
            except OSError:
                pass
        for line in p.stdout:
            line = ANSI.sub("", line.rstrip("\r\n"))
            if not line:
                continue
            tag = "err" if re.search(r"\b(error|failed|fatal)\b", line, re.I) else ("warn" if re.search(r"warning", line, re.I) else None)
            log(line, tag)
            m = NINJA.search(line)
            if m and stage is not None and int(m.group(2)) > 0:
                lo, hi = STAGES[stage][1], STAGES[stage][2]
                set_pct(lo + (hi - lo) * min(int(m.group(1)) / float(m.group(2)), 1.0))
        p.wait()
        st["proc"] = None
        return p.returncode

    def git_out(args, cwd):
        try:
            r = srun(["git"] + args, cwd=cwd, capture_output=True, text=True, **popen_flags())
            return r.stdout.strip() if r.returncode == 0 else ""
        except OSError:
            return ""

    def check_flow():
        st["pause"].wait()
        if st["cancel"]:
            raise RuntimeError("cancelled")

    def need_git():
        if not tool_ok("git"):
            raise RuntimeError("Git program not found. Press Search next to 'Git program' (or install Git for Windows).")

    def step_prepare(ctx, need_docker=True):
        enter_stage(0)
        src = v_src.get().strip().strip('"')
        if not src or not os.path.exists(src):
            raise RuntimeError("Source path does not exist. Press Search next to 'ESP-Miner source'.")
        if os.path.isfile(src):
            clean_tmp(False)
            st["tmp"] = tempfile.mkdtemp(prefix="seleneforge_")
            log("Extracting %s -> %s" % (os.path.basename(src), st["tmp"]))
            if not extract_archive(src, st["tmp"], log):
                raise RuntimeError("Could not extract archive. For .rar/.7z press Search next to '7-Zip'.")
            base = st["tmp"]
        else:
            base = src
        r = find_firmware_root(base)
        if not r:
            raise RuntimeError("No valid ESP-Miner project found (need CMakeLists.txt, main/ and partitions.csv or sdkconfig.defaults).")
        ctx["root"] = r
        ctx["ver"] = read_version(r)
        notify("Valid firmware source: %s (version %s)" % (r, ctx["ver"]))
        if not need_docker:
            return
        img = pick_idf_image(r) if v_auto.get() else (v_img.get().strip() or DEFAULT_IMAGE)
        if v_auto.get():
            root.after(0, lambda: v_img.set(img))
        ctx["image"] = img
        log("ESP-IDF image: " + img, "gold")
        if not tool_ok("docker"):
            raise RuntimeError("Docker program not found. Press Search next to 'Docker program'.")
        if run_cmd(["docker", "info"], show=False) != 0:
            raise RuntimeError("Docker daemon is not running. Start Docker Desktop and retry.")

    def repair_wrapper(r, dest, ver):
        """Make sure components/libsecp256k1/CMakeLists.txt exists, otherwise ESP-IDF cannot see the 'libsecp256k1' component."""
        wrap = wrapper_component_dir(dest)
        if not wrap:
            return
        cm = os.path.join(wrap, "CMakeLists.txt")
        if os.path.isfile(cm):
            notify("libsecp256k1 component file present: " + os.path.relpath(cm, r))
            return
        txt, src = official_wrapper_cmake(ver)
        os.makedirs(wrap, exist_ok=True)
        with open(cm, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        notify("Restored missing %s (from %s)." % (os.path.relpath(cm, r), src), "warn")

    def clone_secp_pinned(clone, pin):
        """Clone libsecp256k1 to exactly `pin` (or latest master when pin is None)."""
        if not pin:
            return run_cmd(["git", "clone", "--depth", "1", SECP_URL, clone]) == 0
        os.makedirs(clone, exist_ok=True)
        steps = [["git", "init", "-q"], ["git", "remote", "add", "origin", SECP_URL],
                 ["git", "fetch", "--depth", "1", "origin", pin], ["git", "checkout", "-q", "FETCH_HEAD"]]
        if all(run_cmd(s, cwd=clone) == 0 for s in steps):
            return True
        log("Pinned shallow fetch failed - trying a full clone and checkout.", "warn")
        rmtree_force(clone)
        if run_cmd(["git", "clone", SECP_URL, clone]) != 0:
            return False
        return run_cmd(["git", "checkout", "-q", pin], cwd=clone) == 0

    def ensure_secp(r, mods, ver):
        need_git()
        targets = secp_targets(r, mods)
        pin, pin_src = pinned_secp_commit(ver)
        if pin:
            log("ESP-Miner %s pins libsecp256k1 commit %s (%s)" % (ver, pin[:12], pin_src), "dim")
        else:
            log("Pinned libsecp256k1 commit unknown for version '%s' - will use latest master." % ver, "warn")
        for dest in targets:
            rel = os.path.relpath(dest, r)
            check_flow()
            if secp_ok(dest):
                notify("libsecp256k1 present: " + rel)
                head = git_out(["rev-parse", "HEAD"], dest) if os.path.exists(os.path.join(dest, ".git")) else ""
                if head and pin and head != pin:
                    log("WARNING: %s is at %s but this release pins %s. Build may fail." % (rel, head[:12], pin[:12]), "warn")
                elif head and pin:
                    log("libsecp256k1 commit matches the pin.", "ok")
            else:
                log("libsecp256k1 missing at %s - cloning %s" % (rel, SECP_URL), "warn")
                scratch = tempfile.mkdtemp(prefix="sf_secp_")
                try:
                    clone = os.path.join(scratch, "secp256k1")
                    if not clone_secp_pinned(clone, pin) or not secp_ok(clone):
                        raise RuntimeError("git clone of libsecp256k1 failed.")
                    os.makedirs(dest, exist_ok=True)
                    shutil.copytree(clone, dest, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
                finally:
                    rmtree_force(scratch)
                if not secp_ok(dest):
                    raise RuntimeError("libsecp256k1 copy incomplete at " + rel)
                notify("libsecp256k1 cloned%s and added: %s" % (" at pinned commit " + pin[:12] if pin else "", rel))
            repair_wrapper(r, dest, ver)

    def step_submodules(ctx):
        enter_stage(1)
        need_git()
        r = ctx["root"]
        mods = parse_gitmodules(r)
        if os.path.isdir(os.path.join(r, ".git")):
            check_flow()
            if run_cmd(["git", "submodule", "update", "--init", "--recursive"], cwd=r) != 0:
                log("git submodule update failed - falling back to direct clones.", "warn")
        for m in mods:
            check_flow()
            if "secp256k1" in (m["path"] + m["url"]).lower():
                continue
            dest = os.path.join(r, m["path"])
            if os.path.isdir(dest) and os.listdir(dest):
                log("Submodule present: " + m["path"], "dim")
                continue
            scratch = tempfile.mkdtemp(prefix="sf_sub_")
            try:
                clone = os.path.join(scratch, "c")
                cmd = ["git", "clone", "--depth", "1", "--recursive"] + (["-b", m["branch"]] if m.get("branch") else []) + [m["url"], clone]
                if run_cmd(cmd) != 0:
                    raise RuntimeError("Clone failed: " + m["url"])
                os.makedirs(dest, exist_ok=True)
                shutil.copytree(clone, dest, ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
            finally:
                rmtree_force(scratch)
        ensure_secp(r, mods, ctx["ver"])
        notify("Submodules and libsecp256k1 ready.")

    def step_secp_only(ctx):
        enter_stage(1)
        ensure_secp(ctx["root"], parse_gitmodules(ctx["root"]), ctx["ver"])

    def docker_run(ctx, script, stage=None):
        name = "seleneforge_%d_%d" % (os.getpid(), int(time.time() * 1000) % 100000)
        st["container"] = name
        rc = run_cmd(["docker", "run", "--rm", "--name", name, "-v", "%s:/project" % ctx["root"], "-w", "/project", ctx["tag"], "bash", "-c", script], stage=stage)
        st["container"] = None
        return rc

    def step_image(ctx):
        enter_stage(2)
        tag = "seleneforge-" + ctx["image"].split("/")[-1].replace(":", "-")
        ctx["tag"] = tag
        if run_cmd(["docker", "image", "inspect", tag], show=False) == 0:
            notify("Forge image ready: " + tag)
            return
        log("Building forge image (one-time: pulls ESP-IDF, adds Node.js). This can take a while.", "warn")
        df = ("FROM %s\nRUN apt-get update && apt-get install -y curl ca-certificates gnupg git "
              "&& curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs "
              "&& rm -rf /var/lib/apt/lists/*\n" % ctx["image"])
        if run_cmd(["docker", "build", "-t", tag, "-"], stdin_text=df) != 0:
            raise RuntimeError("Docker image build failed.")
        notify("Forge image built: " + tag)

    def step_build(ctx):
        enter_stage(3)
        ensure_secp(ctx["root"], parse_gitmodules(ctx["root"]), ctx["ver"])
        chown = "" if IS_WIN else "; chown -R %d:%d /project 2>/dev/null || true" % (os.getuid(), os.getgid())
        script = ("git config --global --add safe.directory '*'; "
                  "if [ ! -f sdkconfig ]; then idf.py set-target esp32s3 || exit $?; fi; "
                  "idf.py build; rc=$?" + chown + "; exit $rc")
        rc = docker_run(ctx, script, stage=3)
        if st["cancel"]:
            raise RuntimeError("cancelled")
        if rc != 0:
            raise RuntimeError("Firmware build failed (exit %d). Press Copy log and send me the first error lines." % rc)
        b = os.path.join(ctx["root"], "build", "esp-miner.bin")
        if not os.path.isfile(b):
            raise RuntimeError("Build finished but build/esp-miner.bin is missing.")
        notify("Firmware compiled: esp-miner.bin (%d KB)" % (os.path.getsize(b) // 1024))

    def step_www(ctx):
        enter_stage(4)
        b = os.path.join(ctx["root"], "build")
        ptp = os.path.join(b, "partition_table", "partition-table.bin")
        ctx["parts"] = parse_partition_table(ptp) if os.path.isfile(ptp) else []
        for p in ctx["parts"]:
            log("  %-10s type=%d sub=0x%02x off=0x%06x size=0x%06x" % (p["name"], p["type"], p["subtype"], p["offset"], p["size"]), "dim")
        www = next((p for p in ctx["parts"] if p["name"] == "www"), None)
        ctx["www"] = www
        www_bin = os.path.join(b, "www.bin")
        if os.path.isfile(www_bin):
            notify("www.bin found from the build.")
            return
        if not www:
            notify("No 'www' partition in this layout - web UI is embedded in esp-miner.bin. No www.bin needed.", "warn")
            return
        if ver_tuple(ctx["ver"]) >= (2, 15, 0):
            notify("Version %s embeds AxeOS in esp-miner.bin - skipping www.bin (not required)." % ctx["ver"], "warn")
            ctx["www"] = None
            return
        dist = find_web_dist(ctx["root"])
        if not dist:
            log("A www partition exists but no built web UI (dist/index.html) was found. Continuing WITHOUT www.bin.", "warn")
            ctx["www"] = None
            return
        rel = os.path.relpath(dist, ctx["root"]).replace(os.sep, "/")
        log("Generating www.bin from %s via spiffsgen" % rel, "gold")
        script = "python $IDF_PATH/components/spiffs/spiffsgen.py %d '%s' build/www.bin" % (www["size"], rel)
        if docker_run(ctx, script) != 0 or not os.path.isfile(www_bin):
            log("spiffsgen failed - continuing without www.bin.", "warn")
            ctx["www"] = None
            return
        notify("www.bin generated (%d KB)." % (os.path.getsize(www_bin) // 1024))

    def step_merge(ctx):
        enter_stage(5)
        b = os.path.join(ctx["root"], "build")
        fa = os.path.join(b, "flasher_args.json")
        if not os.path.isfile(fa):
            raise RuntimeError("build/flasher_args.json missing - cannot create factory image.")
        j = json.load(open(fa, encoding="utf-8"))
        files = dict(j.get("flash_files", {}))
        fs = j.get("flash_settings", {})
        chip = j.get("extra_esptool_args", {}).get("chip", "esp32s3")
        used = {int(k, 16) for k in files}
        have_names = {os.path.basename(v) for v in files.values()}
        parts = ctx.get("parts") or []
        cfg = v_cfg.get().strip().strip('"')
        if cfg:
            nvs = next((p for p in parts if p["name"] == "nvs"), None)
            if not nvs:
                raise RuntimeError("config.csv given but no nvs partition found in the table.")
            shutil.copy(cfg, os.path.join(b, "sf_config.csv"))
            gen = ("(python -m esp_idf_nvs_partition_gen generate build/sf_config.csv build/nvs.bin 0x%x || "
                   "python $IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py generate build/sf_config.csv build/nvs.bin 0x%x)" % (nvs["size"], nvs["size"]))
            if docker_run(ctx, gen) != 0 or not os.path.isfile(os.path.join(b, "nvs.bin")):
                raise RuntimeError("NVS image generation from config.csv failed.")
            files["0x%x" % nvs["offset"]] = "nvs.bin"
            used.add(nvs["offset"])
            notify("NVS config image generated at 0x%x." % nvs["offset"])
        www = ctx.get("www")
        if www and os.path.isfile(os.path.join(b, "www.bin")) and "www.bin" not in have_names and www["offset"] not in used:
            files["0x%x" % www["offset"]] = "www.bin"
        pairs = " ".join("%s %s" % (k, "build/" + v.replace("\\", "/")) for k, v in sorted(files.items(), key=lambda kv: int(kv[0], 16)))
        out = "build/seleneforge-factory.bin"
        common = "--flash-mode %s --flash-size %s --flash-freq %s" % (fs.get("flash_mode", "dio"), fs.get("flash_size", "16MB"), fs.get("flash_freq", "80m"))
        old = common.replace("--flash-", "--flash_")
        script = ("python -m esptool --chip %s merge-bin -o %s %s %s || "
                  "python -m esptool --chip %s merge_bin -o %s %s %s" % (chip, out, common, pairs, chip, out, old, pairs))
        log("Merging: " + pairs, "dim")
        if docker_run(ctx, script) != 0 or not os.path.isfile(os.path.join(b, "seleneforge-factory.bin")):
            raise RuntimeError("Factory image merge failed. Press Copy log and send me the error.")
        notify("Factory image merged.")

    def step_export(ctx):
        enter_stage(6)
        b = os.path.join(ctx["root"], "build")
        out = v_out.get().strip().strip('"')
        os.makedirs(out, exist_ok=True)
        ver = ctx["ver"]
        done = []
        for src, dst in (("seleneforge-factory.bin", "seleneforge-factory-%s.bin" % ver), ("esp-miner.bin", "esp-miner-%s.bin" % ver), ("www.bin", "www-%s.bin" % ver)):
            sp = os.path.join(b, src)
            if os.path.isfile(sp):
                shutil.copy(sp, os.path.join(out, dst))
                done.append(dst)
        set_pct(100)
        notify("Exported to %s: %s" % (out, ", ".join(done)))
        log("Flash with:  bitaxetool --firmware \"%s\"   (or OTA esp-miner.bin via AxeOS)" % os.path.join(out, "seleneforge-factory-%s.bin" % ver), "gold")

    PLANS = {
        "full": [step_prepare, step_submodules, step_image, step_build, step_www, step_merge, step_export],
        "sub": [lambda c: step_prepare(c, False), step_submodules],
        "secp": [lambda c: step_prepare(c, False), step_secp_only],
    }

    def worker(steps):
        ctx, ok = {}, False
        try:
            for s in steps:
                check_flow()
                s(ctx)
            ok = True
        except Exception as e:
            if str(e) == "cancelled":
                log("\u16df The forge was quenched (cancelled).", "warn")
            else:
                log("\u2716 " + str(e), "err")
        q.put(("done", ok))

    def set_running(on):
        for b in (b_start, b_sub, b_secp, b_valid, b_clean):
            b.config(state="disabled" if on else "normal")
        b_pause.config(state="normal" if on else "disabled", text="Pause")
        b_cancel.config(state="normal" if on else "disabled")

    def start(plan, label):
        if st["thread"] and st["thread"].is_alive():
            return
        sync_tools()
        save_settings()
        st.update(cancel=False, max_pct=0.0, target=0.0, shown=0.0)
        st["pause"].set()
        set_running(True)
        log("\u16cb\u16d6\u16da\u16d6\u16be\u16d6 \u2014 %s begins" % label, "rune")
        t = threading.Thread(target=worker, args=(PLANS[plan],), daemon=True)
        st["thread"] = t
        t.start()

    def finish(ok):
        set_running(False)
        if ok:
            if st["stage"] == len(STAGES) - 1:
                st["max_pct"] = st["target"] = 100.0
            status_var.set("Hail Allfather - the forge is quiet.")
            log("\u16cb\u16d6\u16da\u16d6\u16be\u16d6 \u2014 complete.", "rune")
            if st["tmp"] and st["stage"] == len(STAGES) - 1 and messagebox.askyesno("Selene Forge", "Delete temporary extracted files?\n" + st["tmp"]):
                clean_tmp(False)
        else:
            status_var.set("The forge stopped. Check the log.")

    def toggle_pause():
        if st["pause"].is_set():
            st["pause"].clear()
            b_pause.config(text="Resume")
            if st["container"]:
                srun(["docker", "pause", st["container"]], capture_output=True, **popen_flags())
            log("Paused.", "warn")
        else:
            if st["container"]:
                srun(["docker", "unpause", st["container"]], capture_output=True, **popen_flags())
            st["pause"].set()
            b_pause.config(text="Pause")
            log("Resumed.", "ok")

    def cancel():
        st["cancel"] = True
        if st["container"]:
            srun(["docker", "unpause", st["container"]], capture_output=True, **popen_flags())
            srun(["docker", "kill", st["container"]], capture_output=True, **popen_flags())
        if st["proc"]:
            try:
                st["proc"].terminate()
            except OSError:
                pass
        st["pause"].set()

    def do_validate():
        sync_tools()
        src = v_src.get().strip().strip('"')
        if not src or not os.path.exists(src):
            return messagebox.showerror("Selene Forge", "Pick a source folder or archive first (use Search beside it).")
        tmp, base = None, src
        if os.path.isfile(src):
            st["origin"] = src
            clean_tmp(False)
            tmp = st["tmp"] = tempfile.mkdtemp(prefix="seleneforge_")
            if not extract_archive(src, tmp, log):
                return messagebox.showerror("Selene Forge", "Could not extract the archive. Set the 7-Zip path for .rar/.7z.")
            base = tmp
        r = find_firmware_root(base)
        if r:
            ver = read_version(r)
            mods = parse_gitmodules(r)
            miss = [os.path.relpath(d, r) for d in secp_targets(r, mods) if not secp_ok(d)]
            nowrap = [os.path.relpath(os.path.join(wrapper_component_dir(d), "CMakeLists.txt"), r) for d in secp_targets(r, mods)
                      if wrapper_component_dir(d) and not os.path.isfile(os.path.join(wrapper_component_dir(d), "CMakeLists.txt"))]
            log("Valid ESP-Miner source: %s  (version %s, image %s)" % (r, ver, pick_idf_image(r)), "ok")
            log("libsecp256k1: " + ("MISSING at " + ", ".join(miss) + " - will be cloned" if miss else "present"), "warn" if miss else "ok")
            if nowrap:
                log("Component file MISSING: %s - will be restored" % ", ".join(nowrap), "warn")
            for n, label in (("docker", "Docker"), ("git", "Git")):
                log("%s program: %s" % (label, TOOLS[n] if tool_ok(n) else "NOT FOUND - press Search"), "ok" if tool_ok(n) else "err")
            if tmp:
                v_src.set(r)
                log("Archive extracted to temp; source now points at the extracted copy. To start fresh, put the archive path back in the source line.", "dim")
        else:
            log("Not a valid firmware source.", "err")
            messagebox.showerror("Selene Forge", "No valid ESP-Miner project found.")

    def clean_tmp(ask):
        t = st["tmp"]
        if not t or not os.path.isdir(t):
            st["tmp"] = None
            if ask:
                log("No temp files to remove.", "dim")
            return
        if ask and not messagebox.askyesno("Selene Forge", "Delete temp folder?\n" + t):
            return
        rmtree_force(t)
        st["tmp"] = None
        log("Temp files removed.", "ok")

    def copy_log():
        root.clipboard_clear()
        root.clipboard_append(console.get("1.0", "end"))
        status_var.set("Log copied to clipboard.")

    def open_out():
        d = v_out.get().strip()
        os.makedirs(d, exist_ok=True)
        if IS_WIN:
            os.startfile(d)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", d])
        else:
            subprocess.Popen(["xdg-open", d])

    def on_close():
        if st["thread"] and st["thread"].is_alive():
            if not messagebox.askyesno("Selene Forge", "A build is running. Cancel it and exit?"):
                return
            cancel()
        save_settings()
        if st["tmp"] and os.path.isdir(st["tmp"]) and messagebox.askyesno("Selene Forge", "Delete temporary extracted files?\n" + st["tmp"]):
            rmtree_force(st["tmp"])
        root.destroy()

    sync_tools()
    root.protocol("WM_DELETE_WINDOW", on_close)
    log("\u16cb\u16d6\u16da\u16d6\u16be\u16d6 \u16a0\u16df\u16b1\u16b7\u16d6 v%s awakens. Every line has its own Browse and Search button." % VERSION, "rune")
    for n, label in (("docker", "Docker"), ("git", "Git")):
        log("%s program: %s" % (label, TOOLS[n] if tool_ok(n) else "not found - press its Search button"), "dim" if tool_ok(n) else "warn")
    log("libsecp256k1 is cloned at the exact commit your ESP-Miner release pins, and its component file is repaired if missing.", "dim")
    log("AxeOS/ESP-Miner 2.15.0+ embeds the web UI in esp-miner.bin, so no separate www.bin is needed.", "dim")
    draw_bar()
    pump()
    root.mainloop()


if __name__ == "__main__":
    run_gui()
