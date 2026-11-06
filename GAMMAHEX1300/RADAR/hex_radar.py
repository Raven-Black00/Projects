#!/usr/bin/env python3
"""
HEX RADAR - session-best radar for a Bitaxe Gamma Hex 1300 (AxeOS)
Phosphor-green CRT radar scope, sonar ping + ntfy push on every new session-best difficulty.
Blip colours: green < 100M, bright red >= 100M, bright yellow >= 1B.

Usage:
  python hex_radar.py                 (uses saved settings, default 192.168.2.45)
  python hex_radar.py --ip 192.168.2.45
  python hex_radar.py --ntfy https://ntfy.sh/mytopic   (or just: --ntfy mytopic)
  python hex_radar.py --demo          (fake data, no miner needed)
  python hex_radar.py --minimized     (start hidden in the tray; used by autostart)
  python hex_radar.py --no-sound      (silence the sonar ping)
Keys: F11 fullscreen | Esc leave fullscreen | Space = test ping | S = settings | Q quit

Tray support needs two small packages (optional - the app still runs without them):
  pip install pystray pillow
Without them, the close button minimizes to the taskbar instead of the tray.
Everything else uses only the standard library. Settings are saved to ~/.hex_radar.json
"""
import argparse, json, math, os, queue, random, re, shutil, struct, subprocess
import sys, tempfile, threading, time, urllib.request, wave
import tkinter as tk
from tkinter import messagebox

try:
    import pystray
    from PIL import Image, ImageDraw
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False

APP_NAME = "HexRadar"
DEFAULT_IP = "192.168.2.45"
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hex_radar.json")
GREEN = (0, 200, 60)      # deep phosphor green
RED = (255, 30, 30)       # bright red
YELLOW = (255, 235, 0)    # bright yellow
RED_MIN = 100e6           # difficulty >= 100M -> red
YELLOW_MIN = 1e9          # difficulty >= 1B -> yellow (overrides red)
BG = "#010803"            # near-black green
DISC = "#031a08"          # scope face
BEZEL = "#07110a"
SUFFIX = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
MAX_BLIPS = 50
TRAIL_STEPS, TRAIL_SPAN = 36, 75


# ---------------------------------------------------------------- helpers
def parse_diff(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^\s*(\d+(?:\.\d+)?|\.\d+)\s*([KMGTPE]?)", str(v), re.I)
    if not m:
        return None
    try:
        return float(m.group(1)) * SUFFIX.get(m.group(2).upper(), 1.0)
    except ValueError:
        return None


def fmt_diff(x):
    if x is None:
        return "---"
    for s, f in (("E", 1e18), ("P", 1e15), ("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if x >= f:
            return f"{x / f:.2f}{s}"
    return f"{x:.0f}"


def shade(a, g=GREEN):
    a = max(0.0, min(1.0, a))
    return "#%02x%02x%02x" % (int(g[0] * a), int(g[1] * a), int(g[2] * a))


def tier_color(v):
    """Blip colour for a difficulty value."""
    if v is not None and v >= YELLOW_MIN:
        return YELLOW
    if v is not None and v >= RED_MIN:
        return RED
    return GREEN


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg):
    """Atomic write so a crash can never leave a half-written settings file."""
    tmp = CONFIG_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        print("Could not save settings:", e)
        try:
            os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------- autostart
def _launch_command():
    """Command line used to start this app at login (always with --minimized)."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return [sys.executable, "--minimized"]
    exe = sys.executable
    if sys.platform.startswith("win"):
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")  # no console window
        if os.path.exists(pyw):
            exe = pyw
    return [exe, os.path.abspath(__file__), "--minimized"]


def _win_cmdline(parts):
    return " ".join('"%s"' % p for p in parts)


def _desktop_path():
    return os.path.join(os.path.expanduser("~"), ".config", "autostart", "hex-radar.desktop")


def autostart_enabled():
    try:
        if sys.platform.startswith("win"):
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_NAME)
            return True
        if sys.platform.startswith("linux"):
            return os.path.exists(_desktop_path())
    except Exception:
        pass
    return False


def set_autostart(enable):
    """Returns (ok, info)."""
    try:
        if sys.platform.startswith("win"):
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Run",
                                 0, winreg.KEY_SET_VALUE)
            with key:
                if enable:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ,
                                      _win_cmdline(_launch_command()))
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            return True, "ok"
        if sys.platform.startswith("linux"):
            p = _desktop_path()
            if enable:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write("[Desktop Entry]\nType=Application\nName=Hex Radar\n"
                            "Exec=%s\nX-GNOME-Autostart-enabled=true\nTerminal=false\n"
                            % " ".join('"%s"' % c for c in _launch_command()))
            elif os.path.exists(p):
                os.remove(p)
            return True, "ok"
        return False, "Autostart is only supported on Windows and Linux"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------- settings shared between GUI and threads
def clean_host(ip):
    """Accept '192.168.2.45', 'http://192.168.2.45/' or 'bitaxe.local'."""
    ip = re.sub(r"^\s*https?://", "", (ip or "").strip(), flags=re.I)
    return ip.strip().strip("/")


class Settings:
    def __init__(self, ip, ntfy):
        self.ip = clean_host(ip)
        self.ntfy = (ntfy or "").strip()
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            return self.ip, self.ntfy

    def set(self, ip, ntfy):
        with self.lock:
            self.ip, self.ntfy = clean_host(ip), (ntfy or "").strip()


# ---------------------------------------------------------------- ntfy
def ntfy_url(target):
    t = (target or "").strip()
    if not t:
        return None
    if re.match(r"^https?://", t, re.I):
        return t
    first = t.split("/")[0]
    if "." in first or ":" in first:       # looks like a host (ntfy.sh/topic, 10.0.0.2:8080/topic)
        local = ":" in first or re.match(r"^\d{1,3}(\.\d{1,3}){3}$", first)
        return ("http://" if local else "https://") + t.strip("/")
    return "https://ntfy.sh/" + t.strip("/")


def send_ntfy(target, message, title="Hex Radar", priority="high", tags="radar,tada", on_done=None):
    """POST in a background thread. on_done(ok, info) is called from that thread
    (it must only touch thread-safe things such as a queue)."""
    url = ntfy_url(target)

    def go():
        if not url:
            ok, info = False, "No ntfy address set"
        else:
            try:
                req = urllib.request.Request(
                    url, data=message.encode("utf-8"), method="POST",
                    headers={"Title": title, "Priority": priority, "Tags": tags})
                with urllib.request.urlopen(req, timeout=8) as r:
                    ok, info = 200 <= r.status < 300, f"HTTP {r.status}"
            except Exception as e:
                ok, info = False, str(e)
        if on_done:
            try:
                on_done(ok, info)
            except Exception:
                pass

    threading.Thread(target=go, daemon=True).start()


# ---------------------------------------------------------------- sonar ping sound
def make_sonar_wav(path, rate=44100):
    """Classic submarine active-sonar ping: pure ~1 kHz tone, soft attack, long
    exponential decay with a faint 2nd harmonic, plus two fading echoes and a
    slight downward 'bloom'. Normalised loud (~95% full scale)."""
    total = 3.6
    n = int(rate * total)
    buf = [0.0] * n

    def add_ping(t0, gain, f0=1000.0):
        start = int(t0 * rate)
        dur = 2.0
        for i in range(int(dur * rate)):
            j = start + i
            if j >= n:
                break
            t = i / rate
            env = min(1.0, t / 0.004) * math.exp(-t * 3.2)
            f = f0 * (1.0 - 0.012 * (1 - math.exp(-t * 6)))
            ph = 2 * math.pi * f * t
            s = math.sin(ph) + 0.18 * math.sin(2 * ph) + 0.05 * math.sin(3 * ph)
            buf[j] += gain * env * s

    add_ping(0.00, 1.00)
    add_ping(0.85, 0.30)
    add_ping(1.70, 0.12)

    peak = max(abs(x) for x in buf) or 1.0
    scale = 0.95 * 32767 / peak
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(x * scale)) for x in buf))
    os.replace(tmp, path)


class Sound:
    """The WAV is built once, cached in the temp dir, and generated off the UI thread."""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.path = os.path.join(tempfile.gettempdir(), "hexradar_sonar_ping.wav")
        self.ok = False
        if not enabled:
            return
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > 1000:
                self.ok = True
                return
        except Exception:
            pass
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        try:
            make_sonar_wav(self.path)
            self.ok = True
        except Exception as e:
            print("Could not build sonar sound:", e)
            self.ok = False

    def play(self):
        if self.enabled:
            threading.Thread(target=self._play, daemon=True).start()

    def _play(self):
        if self.ok:
            try:
                if sys.platform.startswith("win"):
                    import winsound
                    winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                if sys.platform == "darwin":
                    subprocess.Popen(["afplay", self.path], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                    return
                for cmd in (["paplay"], ["aplay", "-q"], ["play", "-q"],
                            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
                    if shutil.which(cmd[0]):
                        subprocess.Popen(cmd + [self.path], stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        return
            except Exception:
                pass
        try:
            import winsound
            winsound.Beep(1000, 400)
        except Exception:
            pass


# ---------------------------------------------------------------- poller
class Poller(threading.Thread):
    def __init__(self, settings, interval, demo, out):
        super().__init__(daemon=True)
        self.settings, self.interval, self.demo, self.out = settings, max(0.5, interval), demo, out
        self.fake = 1.2e6

    def run(self):
        while True:
            try:
                if self.demo:
                    if random.random() < 0.25:
                        self.fake *= random.uniform(1.5, 6)
                        if self.fake > 5e9:          # demo a session reset
                            self.fake = 1.2e6
                    data = {"bestSessionDiff": self.fake,
                            "hashRate": random.uniform(1250, 1350),
                            "temp": random.uniform(52, 58),
                            "power": random.uniform(55, 62)}
                else:
                    ip, _ = self.settings.get()
                    with urllib.request.urlopen(f"http://{ip}/api/system/info", timeout=4) as r:
                        data = json.load(r)
                    if not isinstance(data, dict):
                        raise ValueError("unexpected reply")
                self.out.put(("ok", data))
            except Exception as e:
                self.out.put(("err", str(e)))
            time.sleep(self.interval)


# ---------------------------------------------------------------- tray icon image
def make_tray_image(size=64):
    img = Image.new("RGBA", (size, size), (1, 8, 3, 255))
    d = ImageDraw.Draw(img)
    c = size // 2
    g = (0, 200, 60, 255)
    for r in (size * 0.45, size * 0.30, size * 0.15):
        d.ellipse((c - r, c - r, c + r, c + r), outline=g, width=2)
    d.line((c, c, c + size * 0.40, c - size * 0.28), fill=(120, 255, 150, 255), width=3)
    d.ellipse((c + size * 0.18, c - size * 0.22, c + size * 0.28, c - size * 0.12), fill=g)
    return img


# ---------------------------------------------------------------- app
class Radar:
    def __init__(self, root, args, settings, cfg):
        self.root, self.args, self.settings = root, args, settings
        self.cfg = cfg
        self.sound = Sound(enabled=not args.no_sound)
        self.c = tk.Canvas(root, bg=BG, highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.q = queue.Queue()
        self.cmdq = queue.Queue()          # commands from tray / worker threads (thread-safe bridge)
        Poller(settings, args.interval, args.demo, self.q).start()

        self.angle = 0.0
        self.rpm = max(0.1, args.rpm)
        self.last = time.monotonic()
        self.best = None
        self.baseline = None
        self.drop_count = 0                # consecutive low readings (glitch filter)
        self.static_key = None             # canvas size the static layer was built for
        self.last_repaint = time.monotonic()
        self.status = "ACQUIRING..."
        self.ntfy_status = "OFF"
        self.hash = self.temp = self.power = None
        self.blips, self.pings = [], []
        self.flash = 0.0
        self.flash_rgb = GREEN
        self.pings_total = 0
        self.win = None
        self.set_msg = None
        self.hidden = False
        self.closing = False
        self.tray = None
        self.geom = None
        self.hud_cache = {}
        self.glow_arcs = []

        self.menubtn = tk.Button(root, text="\u2630 MENU", command=self.open_settings, bg=BG,
                                 fg=shade(1), activebackground=shade(0.3),
                                 activeforeground=shade(1), relief="solid", bd=1,
                                 font=("Consolas", 11, "bold"), cursor="hand2")
        self.menubtn.place(relx=1.0, x=-15, y=15, anchor="ne")

        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not bool(root.attributes("-fullscreen"))))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.bind("<space>", lambda e: self.trigger(self.best or 0, test=True))
        root.bind("s", lambda e: self.open_settings())
        root.bind("q", lambda e: self.quit())
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Unmap>", self.on_unmap)   # minimize -> stop drawing
        root.bind("<Map>", self.on_map)       # restore -> force a clean repaint

        if cfg.get("tray", True) and HAVE_TRAY:
            self.start_tray()
        if args.minimized:
            root.after(200, self.hide)
        self.tick()

    # ---- tray / window state
    def start_tray(self):
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Show Hex Radar", lambda i, it: self.cmdq.put("show"), default=True),
                pystray.MenuItem("Settings", lambda i, it: self.cmdq.put("settings")),
                pystray.MenuItem("Test ping", lambda i, it: self.cmdq.put("ping")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda i, it: self.cmdq.put("quit")))
            self.tray = pystray.Icon(APP_NAME, make_tray_image(), "Hex Radar", menu)
            self.tray.run_detached()
        except Exception as e:
            print("Tray unavailable:", e)
            self.tray = None

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()
        self.hidden = True

    def show(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()
        self.hidden = False

    def on_unmap(self, e):
        if e.widget is self.root and self.root.state() in ("iconic", "withdrawn"):
            self.hidden = True

    def on_map(self, e):
        if e.widget is self.root:
            self.hidden = False
            self.static_key = None            # rebuild static layer
            self.c.delete("dyn")
            self.c.update_idletasks()

    def on_close(self):
        """Close button = hide to tray (or minimize if no tray)."""
        if self.cfg.get("close_to_tray", True):
            self.hide()
        else:
            self.quit()

    def quit(self):
        if self.closing:
            return
        self.closing = True
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run_cmds(self):
        """Drain the thread-safe command queue. Returns False once the app is quitting."""
        while True:
            try:
                cmd = self.cmdq.get_nowait()
            except queue.Empty:
                break
            if isinstance(cmd, tuple):
                if cmd[0] == "ntfy_sent":
                    self.ntfy_status = "SENT" if cmd[1] else "FAILED"
                elif cmd[0] == "ntfy_test":
                    self.show_test_result(cmd[1], cmd[2])
            elif cmd == "show":
                self.show()
            elif cmd == "settings":
                self.show()
                self.open_settings()
            elif cmd == "ping":
                self.trigger(self.best or 0, test=True)
            elif cmd == "quit":
                self.quit()
                return False
        return not self.closing

    def show_test_result(self, ok, info):
        try:
            if self.set_msg is not None and self.win is not None and self.win.winfo_exists():
                self.set_msg.config(
                    text=("ntfy OK (" + info + ")") if ok else ("ntfy FAILED: " + info[:60]),
                    fg=shade(1) if ok else "#ff5050")
        except Exception:
            pass

    # ---- settings window
    def open_settings(self):
        if self.win and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return
        w = self.win = tk.Toplevel(self.root)
        w.title("Hex Radar Settings")
        w.configure(bg=BG, padx=20, pady=16)
        w.resizable(False, False)
        w.transient(self.root)
        ip, nt = self.settings.get()
        lab = dict(bg=BG, fg=shade(0.9), font=("Consolas", 11), anchor="w")
        ent = dict(bg="#041a0a", fg=shade(1), insertbackground=shade(1), relief="solid", bd=1,
                   font=("Consolas", 12), width=44)
        btn = dict(bg=BG, fg=shade(1), activebackground=shade(0.3), activeforeground=shade(1),
                   relief="solid", bd=1, font=("Consolas", 11, "bold"), cursor="hand2", padx=10)
        chk = dict(bg=BG, fg=shade(0.9), activebackground=BG, activeforeground=shade(1),
                   selectcolor="#041a0a", font=("Consolas", 11), anchor="w", cursor="hand2")

        tk.Label(w, text="MINER IP / HOSTNAME", **lab).grid(row=0, column=0, sticky="w")
        ip_var = tk.StringVar(value=ip)
        ip_e = tk.Entry(w, textvariable=ip_var, **ent)
        ip_e.grid(row=1, column=0, columnspan=3, pady=(2, 12), sticky="we")

        tk.Label(w, text="NTFY ADDRESS (topic or full URL)", **lab).grid(row=2, column=0, sticky="w")
        nt_var = tk.StringVar(value=nt)
        nt_e = tk.Entry(w, textvariable=nt_var, **ent)
        nt_e.grid(row=3, column=0, columnspan=3, pady=(2, 4), sticky="we")
        tk.Label(w, text="e.g. https://ntfy.sh/hexradar or http://192.168.2.10:8080/hexradar",
                 bg=BG, fg=shade(0.45), font=("Consolas", 9)).grid(row=4, column=0, columnspan=3, sticky="w")

        auto_var = tk.BooleanVar(value=autostart_enabled())
        tray_var = tk.BooleanVar(value=self.cfg.get("close_to_tray", True))
        tk.Checkbutton(w, text="Start automatically at login", variable=auto_var, **chk
                       ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))
        tray_text = "Close button hides to tray" if HAVE_TRAY else "Close button minimizes (pip install pystray pillow for tray)"
        tk.Checkbutton(w, text=tray_text, variable=tray_var, **chk
                       ).grid(row=6, column=0, columnspan=3, sticky="w")

        msg = tk.Label(w, text="", bg=BG, fg=shade(0.8), font=("Consolas", 10), anchor="w")
        msg.grid(row=8, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.set_msg = msg

        def do_save():
            old_ip, _ = self.settings.get()
            new_ip = clean_host(ip_var.get()) or DEFAULT_IP
            self.settings.set(new_ip, nt_var.get())
            ip2, nt2 = self.settings.get()
            self.cfg.update({"ip": ip2, "ntfy": nt2, "close_to_tray": bool(tray_var.get()),
                             "autostart": bool(auto_var.get())})
            save_config(self.cfg)
            ok, info = set_autostart(bool(auto_var.get()))
            if not ok:
                messagebox.showwarning("Autostart", "Could not change autostart:\n" + info, parent=w)
            if ip2 != old_ip:               # new miner: re-learn baseline, drop stale readings
                self.baseline = None
                self.best = None
                self.hash = self.temp = self.power = None
                self.status = "ACQUIRING..."
            self.ntfy_status = "READY" if nt2 else "OFF"
            w.destroy()

        def do_test_ntfy():
            # side-effect free: tests whatever is typed without saving anything
            msg.config(text="Sending test...", fg=shade(0.8))
            send_ntfy(nt_var.get(), "Hex Radar test message", title="Hex Radar TEST",
                      priority="default", tags="satellite",
                      on_done=lambda ok, info: self.cmdq.put(("ntfy_test", ok, info)))

        tk.Button(w, text="TEST NTFY", command=do_test_ntfy, **btn).grid(row=7, column=0, pady=(14, 0), sticky="w")
        tk.Button(w, text="TEST PING", command=self.sound.play, **btn).grid(row=7, column=1, pady=(14, 0))
        tk.Button(w, text="SAVE", command=do_save, **btn).grid(row=7, column=2, pady=(14, 0), sticky="e")
        w.bind("<Return>", lambda e: do_save())
        w.bind("<Escape>", lambda e: w.destroy())
        ip_e.focus_set()

    # ---- data
    def poll(self):
        while True:
            try:
                kind, d = self.q.get_nowait()
            except queue.Empty:
                break
            if kind == "err":
                self.status = "NO CONTACT"
                self.hash = self.temp = self.power = None     # don't show stale telemetry
                continue
            self.status = "LOCKED"
            self.hash, self.temp, self.power = d.get("hashRate"), d.get("temp"), d.get("power")
            v = parse_diff(d.get("bestSessionDiff"))
            if v is None or v <= 0:        # ignore empty / zero / unparsable glitches
                continue
            if self.baseline is None:
                self.baseline = v
                self.best = v
            elif v > self.baseline:
                self.drop_count = 0
                self.trigger(v)
                self.baseline = v
                self.best = v
            elif v < self.baseline * 0.5:
                # a real session reset must be seen twice in a row before we accept it
                self.drop_count += 1
                if self.drop_count >= 2:
                    self.baseline = v
                    self.best = v
                    self.drop_count = 0
                    self.status = "SESSION RESET"
            else:
                self.drop_count = 0

    def trigger(self, v, test=False):
        a = random.uniform(0, 2 * math.pi)
        r = random.uniform(0.25, 0.9)
        rgb = tier_color(v)
        now = time.monotonic()
        self.pings.append({"t": now, "a": a, "r": r, "rgb": rgb})
        self.blips.append({"a": a, "r": r, "lit": 1.0, "label": fmt_diff(v),
                           "born": now, "rgb": rgb})
        del self.blips[:-MAX_BLIPS]
        self.flash = 1.0
        self.flash_rgb = rgb
        self.sound.play()
        if test:
            return
        self.pings_total += 1
        ip, nt = self.settings.get()
        if nt:
            self.ntfy_status = "SENDING"
            host = "DEMO" if self.args.demo else ip
            send_ntfy(nt, f"New session best: {fmt_diff(v)}\nMiner: {host}",
                      title="Hex Radar - NEW SESSION BEST",
                      on_done=lambda ok, info: self.cmdq.put(("ntfy_sent", ok)))

    # ---- loop
    def tick(self):
        if self.closing or not self.run_cmds():
            return
        now = time.monotonic()
        dt = max(0.0, min(now - self.last, 0.1))   # cap so a stall can't make the arm jump
        self.last = now
        prev = self.angle
        self.angle = (self.angle + dt * self.rpm * 6.0) % 360
        self.poll()
        fade = dt * self.rpm / 60.0                # one full fade per revolution at any RPM
        lo, hi = prev, self.angle
        for b in self.blips:
            deg = math.degrees(b["a"]) % 360
            hit = (lo <= deg <= hi) if lo <= hi else (deg >= lo or deg <= hi)
            if hit:
                b["lit"] = 1.0
            else:
                b["lit"] = max(0.0, b["lit"] - fade)
        self.blips = [b for b in self.blips if now - b["born"] < 600]
        self.pings = [p for p in self.pings if now - p["t"] < 3.5]
        self.flash = max(0.0, self.flash - dt * 1.5)
        if self.hidden or self.root.state() in ("iconic", "withdrawn"):
            self.hidden = True
            self.root.after(250, self.tick)        # no drawing while minimized / in tray
            return
        if now - self.last_repaint > 60:           # watchdog: clear any stale pixels
            self.last_repaint = now
            self.static_key = None
        self.draw(now)
        self.root.after(33, self.tick)             # ~30 fps is plenty for a 10 RPM sweep

    def build_static(self, w, h, R):
        """Rebuilds every persistent canvas item. Items are created bottom-to-top so no
        per-frame tag_raise/tag_lower is needed; per-frame code only reconfigures them."""
        c = self.c
        cx, cy = w / 2, h / 2
        self.geom = (cx, cy, R)
        c.delete("all")
        self.hud_cache = {}

        # base: bezel, glow rings, scope face
        c.create_oval(cx - R - 26, cy - R - 26, cx + R + 26, cy + R + 26,
                      fill=BEZEL, outline=shade(0.18), width=2)
        for k, a in ((10, 0.10), (6, 0.16), (3, 0.24)):
            c.create_oval(cx - R - k, cy - R - k, cx + R + k, cy + R + k, outline=shade(a), width=1)
        c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=DISC, outline="")

        # glow layer: flash disc + sweep trail (reconfigured per frame)
        self.flash_item = c.create_oval(cx - R, cy - R, cx + R, cy + R, fill=DISC, outline="",
                                        state="hidden")
        self.glow_arcs = []
        for i in range(TRAIL_STEPS):
            self.glow_arcs.append(c.create_arc(
                cx - R, cy - R, cx + R, cy + R, start=90, extent=TRAIL_SPAN / TRAIL_STEPS + 1.5,
                style="pieslice", fill=shade(0.50 * (1 - i / TRAIL_STEPS) ** 2.2), outline=""))

        # overlay: rings, spokes, ticks, labels, scanlines
        dim = shade(0.40)
        for k in range(1, 6):
            rr = R * k / 5
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=dim, width=2 if k == 5 else 1)
            if k < 5:
                c.create_text(cx + 4, cy - rr - 2, anchor="sw", text=f"{k * 20}",
                              fill=shade(0.35), font=("Consolas", max(8, int(R / 55))))
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            c.create_line(cx, cy, cx + R * math.sin(a), cy - R * math.cos(a), fill=shade(0.26))
        for deg in range(0, 360, 5):
            a = math.radians(deg)
            ln = 14 if deg % 30 == 0 else 7
            c.create_line(cx + R * math.sin(a), cy - R * math.cos(a),
                          cx + (R - ln) * math.sin(a), cy - (R - ln) * math.cos(a), fill=dim)
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            c.create_text(cx + (R + 22) * math.sin(a), cy - (R + 22) * math.cos(a),
                          text=f"{deg:03d}", fill=shade(0.75), font=("Consolas", max(9, int(R / 40))))
        y = int(cy - R) + 1
        while y < cy + R:
            dy = y - cy
            half = math.sqrt(max(0.0, R * R - dy * dy))
            c.create_line(cx - half, y, cx + half, y, fill="#010a04")
            y += 4

        # live layer: arm, hub, HUD (reconfigured per frame)
        self.arm_glow = c.create_line(cx, cy, cx, cy - R, fill=shade(0.30), width=9)
        self.arm_core = c.create_line(cx, cy, cx, cy - R, fill=shade(0.95), width=2)
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=shade(1), outline="")
        f = ("Consolas", max(10, int(R / 32)))
        fb = ("Consolas", max(14, int(R / 18)), "bold")
        c.create_text(20, 20, anchor="nw", text="HEX RADAR // SESSION BEST", fill=shade(1), font=fb)
        self.hud_body = c.create_text(20, 20 + int(R / 12), anchor="nw", text="",
                                      fill=shade(0.80), font=f)
        c.create_text(w - 20, h - 20, anchor="se",
                      text="F11 fullscreen | SPACE test ping | S settings | Q quit",
                      fill=shade(0.38), font=("Consolas", 10))
        self.banner = c.create_text(cx, 40, text="*** NEW SESSION BEST ***", fill=shade(1),
                                    font=("Consolas", max(16, int(R / 14)), "bold"), state="hidden")

    def hud_text(self):
        hs = f"{self.hash:.0f} GH/s" if isinstance(self.hash, (int, float)) else "---"
        ts = f"{self.temp:.1f} C" if isinstance(self.temp, (int, float)) else "---"
        ps = f"{self.power:.1f} W" if isinstance(self.power, (int, float)) else "---"
        ip, nt = self.settings.get()
        lines = [f"TARGET {'DEMO' if self.args.demo else ip}",
                 f"STATUS {self.status}", f"BEST   {fmt_diff(self.best)}",
                 f"HASH   {hs}", f"ASIC   {ts}", f"POWER  {ps}", f"PINGS  {self.pings_total}",
                 f"NTFY   {self.ntfy_status if nt else 'OFF'}"]
        return "\n".join(lines)

    def draw(self, now):
        c = self.c
        w, h = c.winfo_width(), c.winfo_height()
        R = min(w, h) / 2 - 40
        if R < 50:
            return
        if self.static_key != (w, h):
            self.build_static(w, h, R)
            self.static_key = (w, h)
        cx, cy, R = self.geom
        c.delete("dyn")

        # flash + sweep trail (existing items, just reconfigured)
        if self.flash > 0.01:
            c.itemconfigure(self.flash_item, state="normal", fill=shade(0.16 * self.flash, self.flash_rgb))
        else:
            c.itemconfigure(self.flash_item, state="hidden")
        step = TRAIL_SPAN / TRAIL_STEPS
        for i, item in enumerate(self.glow_arcs):
            c.itemconfigure(item, start=90 - (self.angle - step * i))

        a = math.radians(self.angle)
        ex, ey = cx + R * math.sin(a), cy - R * math.cos(a)
        c.coords(self.arm_glow, cx, cy, ex, ey)
        c.coords(self.arm_core, cx, cy, ex, ey)

        # blips and sonar rings (only these are recreated each frame)
        bfont = ("Consolas", max(10, int(R / 34)), "bold")
        for b in self.blips:
            if b["lit"] <= 0:
                continue
            col = b["rgb"]
            bx = cx + R * b["r"] * math.sin(b["a"])
            by = cy - R * b["r"] * math.cos(b["a"])
            s = 5 + 5 * b["lit"]
            c.create_oval(bx - s * 2, by - s * 2, bx + s * 2, by + s * 2,
                          outline=shade(0.4 * b["lit"], col), tags="dyn")
            c.create_oval(bx - s, by - s, bx + s, by + s,
                          fill=shade(b["lit"], col), outline="", tags="dyn")
            c.create_text(bx + s * 2 + 4, by - s * 2, anchor="w", text=b["label"],
                          fill=shade(b["lit"], col), font=bfont, tags="dyn")
        for p in self.pings:
            age = now - p["t"]
            px = cx + R * p["r"] * math.sin(p["a"])
            py = cy - R * p["r"] * math.cos(p["a"])
            for k in range(3):
                t = age - k * 0.35
                if t < 0:
                    continue
                rr = t * R * 0.28
                c.create_oval(px - rr, py - rr, px + rr, py + rr,
                              outline=shade(max(0.0, 1 - t / 3.0), p["rgb"]), width=3, tags="dyn")

        # HUD: only touch the canvas when the text actually changed
        txt = self.hud_text()
        if self.hud_cache.get("body") != txt:
            c.itemconfigure(self.hud_body, text=txt)
            self.hud_cache["body"] = txt
        if self.flash > 0.4:
            c.itemconfigure(self.banner, state="normal", fill=shade(self.flash, self.flash_rgb))
            self.hud_cache["banner"] = True
        elif self.hud_cache.get("banner"):
            c.itemconfigure(self.banner, state="hidden")
            self.hud_cache["banner"] = False


def main():
    if sys.platform.startswith("win"):            # crisp text on high-DPI displays
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=None, help="Gamma Hex IP or hostname")
    ap.add_argument("--ntfy", default=None, help="ntfy topic or full URL")
    ap.add_argument("--interval", type=float, default=3.0, help="poll seconds")
    ap.add_argument("--rpm", type=float, default=10.0, help="sweep revolutions per minute")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    ap.add_argument("--no-sound", action="store_true", help="disable the sonar ping")
    args = ap.parse_args()
    ip = args.ip or cfg.get("ip") or DEFAULT_IP
    nt = args.ntfy if args.ntfy is not None else cfg.get("ntfy", "")
    settings = Settings(ip, nt)
    root = tk.Tk()
    root.title("Hex Radar")
    root.geometry("1100x1000")
    root.configure(bg=BG)
    Radar(root, args, settings, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
