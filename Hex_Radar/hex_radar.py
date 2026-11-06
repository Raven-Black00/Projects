#!/usr/bin/env python3
"""
HEX RADAR - session-best radar for a Bitaxe Gamma Hex 1300 (AxeOS)
Black screen, green sweep arm, sonar ping + ntfy push on every new session-best difficulty.

Usage:
  python hex_radar.py                 (uses saved settings, default 192.168.2.45)
  python hex_radar.py --ip 192.168.2.45
  python hex_radar.py --ntfy https://ntfy.sh/mytopic    (or just: --ntfy mytopic)
  python hex_radar.py --demo          (fake data, no miner needed)
Keys: F11 fullscreen | Esc leave fullscreen | Space = test ping | S = settings | Q quit
Only the Python standard library is used (tkinter, urllib).
Settings (ip, ntfy, interval, rpm) are saved to ~/.hex_radar.json
"""
import argparse, array, json, math, os, queue, random, re, shutil, subprocess
import sys, tempfile, threading, time, urllib.request, wave
from functools import lru_cache
import tkinter as tk

DEFAULT_IP = "192.168.2.45"
DEFAULT_INTERVAL = 3.0
DEFAULT_RPM = 10.0
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hex_radar.json")
GREEN = (0, 255, 70)
SUFFIX = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}
FRAME_MS = 33          # ~30 fps; sweep motion is dt-driven so it stays smooth
SWEEP_STEPS = 24       # trail slices (was 70)
SWEEP_SPAN = 75

# ---------------------------------------------------------------- helpers
def parse_diff(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.match(r"^\s*([\d.]+)\s*([KMGTPE]?)", str(v), re.I)
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

@lru_cache(maxsize=1024)
def _shade(q, g):
    a = q / 255.0
    return "#%02x%02x%02x" % (int(g[0] * a), int(g[1] * a), int(g[2] * a))

def shade(a, g=GREEN):
    return _shade(int(round(max(0.0, min(1.0, a)) * 255)), g)

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}

def save_config(updates):
    """Merge into the existing file so unrelated keys are never lost."""
    try:
        cfg = load_config()
        cfg.update(updates)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print("Could not save settings:", e)

def base_url(host):
    """Accept 'ip', 'host:port', or 'http(s)://host' and return a clean base URL."""
    host = (host or "").strip().rstrip("/")
    if not re.match(r"^https?://", host, re.I):
        host = "http://" + host
    return host

# ---------------------------------------------------------------- settings shared between GUI and threads
class Settings:
    def __init__(self, ip, ntfy):
        self.ip = ip
        self.ntfy = ntfy
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            return self.ip, self.ntfy

    def set(self, ip, ntfy):
        with self.lock:
            self.ip, self.ntfy = ip.strip(), ntfy.strip()

# ---------------------------------------------------------------- ntfy
def ntfy_url(target):
    target = (target or "").strip()
    if not target:
        return None
    if not re.match(r"^https?://", target, re.I):
        target = "https://ntfy.sh/" + target.strip("/")
    return target

def send_ntfy(target, message, title="Hex Radar", priority="high", tags="radar,tada", on_done=None):
    """POST in a background thread. on_done(ok, info) is called from that thread."""
    url = ntfy_url(target)

    def go():
        if not url:
            if on_done:
                on_done(False, "No ntfy address set")
            return
        try:
            req = urllib.request.Request(
                url, data=message.encode("utf-8"), method="POST",
                headers={"Title": title, "Priority": priority, "Tags": tags})
            with urllib.request.urlopen(req, timeout=8) as r:
                ok = 200 <= r.status < 300
                if on_done:
                    on_done(ok, f"HTTP {r.status}")
        except Exception as e:
            if on_done:
                on_done(False, str(e))

    threading.Thread(target=go, daemon=True).start()

# ---------------------------------------------------------------- sonar ping sound
def make_sonar_wav(path, rate=44100):
    """Classic submarine active-sonar ping: pure ~1 kHz tone, soft attack, long
    exponential decay with a faint 2nd harmonic, plus two fading echoes and a
    slight downward 'bloom'. Normalised loud (~95% full scale)."""
    total = 3.6
    n = int(rate * total)
    buf = [0.0] * n
    two_pi = 2 * math.pi

    def add_ping(t0, gain, f0=1000.0):
        start = int(t0 * rate)
        for i in range(int(2.0 * rate)):
            j = start + i
            if j >= n:
                break
            t = i / rate
            env = min(1.0, t / 0.004) * math.exp(-t * 3.2)
            if env < 1e-4:
                break
            f = f0 * (1.0 - 0.012 * (1 - math.exp(-t * 6)))
            ph = two_pi * f * t
            s = math.sin(ph) + 0.18 * math.sin(2 * ph) + 0.05 * math.sin(3 * ph)
            buf[j] += gain * env * s

    add_ping(0.00, 1.00)
    add_ping(0.85, 0.30)
    add_ping(1.70, 0.12)

    peak = max(abs(x) for x in buf) or 1.0
    scale = 0.95 * 32767 / peak
    pcm = array.array("h", (int(x * scale) for x in buf))
    if sys.byteorder == "big":
        pcm.byteswap()
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)     # atomic: never leaves a half-written cache file

class Sound:
    def __init__(self):
        self.path = os.path.join(tempfile.gettempdir(), "hexradar_sonar_ping_v2.wav")
        self.ok = False
        self.ready = threading.Event()
        self.proc = None
        threading.Thread(target=self._build, daemon=True).start()   # no UI stall at startup

    def _build(self):
        try:
            if not (os.path.exists(self.path) and os.path.getsize(self.path) > 1000):
                make_sonar_wav(self.path)
            self.ok = True
        except Exception as e:
            print("Could not build sonar sound:", e)
            self.ok = False
        self.ready.set()

    def play(self):
        threading.Thread(target=self._play, daemon=True).start()

    def _play(self):
        self.ready.wait(10)
        if self.ok:
            try:
                if sys.platform.startswith("win"):
                    import winsound
                    winsound.PlaySound(self.path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
                if self.proc and self.proc.poll() is None:      # don't stack overlapping players
                    self.proc.terminate()
                if sys.platform == "darwin":
                    self.proc = subprocess.Popen(["afplay", self.path])
                    return
                for cmd in (["paplay"], ["aplay", "-q"], ["play", "-q"],
                            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
                    if shutil.which(cmd[0]):
                        self.proc = subprocess.Popen(cmd + [self.path],
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.settings, self.interval, self.demo, self.out = settings, interval, demo, out
        self.fake = 1.2e6

    def run(self):
        while True:
            try:
                if self.demo:
                    if random.random() < 0.25:
                        self.fake *= random.uniform(1.5, 6)
                    data = {"bestSessionDiff": self.fake, "hashRate": random.uniform(1250, 1350),
                            "temp": random.uniform(52, 58), "power": random.uniform(55, 62)}
                else:
                    ip, _ = self.settings.get()      # re-read every cycle: settings changes apply live
                    with urllib.request.urlopen(base_url(ip) + "/api/system/info", timeout=4) as r:
                        data = json.load(r)
                if not isinstance(data, dict):
                    raise ValueError("unexpected reply")
                self.out.put(("ok", data))
            except Exception as e:
                self.out.put(("err", str(e)))
            time.sleep(max(0.5, self.interval))

# ---------------------------------------------------------------- app
class Radar:
    def __init__(self, root, args, settings):
        self.root, self.args, self.settings = root, args, settings
        self.sound = Sound()
        self.c = tk.Canvas(root, bg="black", highlightthickness=0)
        self.c.pack(fill="both", expand=True)
        self.q = queue.Queue()          # poller -> UI
        self.ui_q = queue.Queue()       # worker threads -> UI (callables run on the Tk thread)
        Poller(settings, args.interval, args.demo, self.q).start()
        self.angle = 0.0
        self.rpm = args.rpm
        self.last = time.time()
        self.best = None
        self.baseline = None
        self.status = "ACQUIRING..."
        self.ntfy_status = "OFF"
        self.hash = self.temp = self.power = None
        self.blips = []
        self.pings = []
        self.flash = 0.0
        self.pings_total = 0
        self.win = None
        self.static_key = None
        self.menu_btn = tk.Button(root, text="\u2630 MENU", command=self.open_settings, bg="black",
                                  fg=shade(1), activebackground=shade(0.3), activeforeground=shade(1),
                                  relief="solid", bd=1, font=("Consolas", 11, "bold"), cursor="hand2")
        self.menu_btn.place(relx=1.0, x=-15, y=15, anchor="ne")
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.bind("<space>", lambda e: self.trigger(self.best, test=True))
        root.bind("s", lambda e: self.open_settings())
        root.bind("q", lambda e: root.destroy())
        self.tick()

    def ui(self, fn):
        """Thread-safe: schedule fn() on the Tk thread."""
        self.ui_q.put(fn)

    # ---- settings window
    def open_settings(self):
        if self.win and self.win.winfo_exists():
            self.win.lift()
            self.win.focus_force()
            return
        w = self.win = tk.Toplevel(self.root)
        w.title("Hex Radar Settings")
        w.configure(bg="black", padx=20, pady=16)
        w.resizable(False, False)
        w.transient(self.root)
        ip, nt = self.settings.get()
        lab = dict(bg="black", fg=shade(0.9), font=("Consolas", 11), anchor="w")
        ent = dict(bg="#041a0a", fg=shade(1), insertbackground=shade(1), relief="solid", bd=1,
                   font=("Consolas", 12), width=44)
        btn = dict(bg="black", fg=shade(1), activebackground=shade(0.3), activeforeground=shade(1),
                   relief="solid", bd=1, font=("Consolas", 11, "bold"), cursor="hand2", padx=10)

        tk.Label(w, text="MINER IP / HOSTNAME", **lab).grid(row=0, column=0, sticky="w")
        ip_var = tk.StringVar(value=ip)
        ip_e = tk.Entry(w, textvariable=ip_var, **ent)
        ip_e.grid(row=1, column=0, columnspan=3, pady=(2, 12), sticky="we")

        tk.Label(w, text="NTFY ADDRESS (topic or full URL)", **lab).grid(row=2, column=0, sticky="w")
        nt_var = tk.StringVar(value=nt)
        nt_e = tk.Entry(w, textvariable=nt_var, **ent)
        nt_e.grid(row=3, column=0, columnspan=3, pady=(2, 4), sticky="we")
        tk.Label(w, text="e.g. https://ntfy.sh/hexradar or http://192.168.2.10:8080/hexradar",
                 bg="black", fg=shade(0.45), font=("Consolas", 9)).grid(row=4, column=0, columnspan=3, sticky="w")

        msg = tk.Label(w, text="", bg="black", fg=shade(0.8), font=("Consolas", 10), anchor="w")
        msg.grid(row=6, column=0, columnspan=3, sticky="we", pady=(10, 0))

        def apply():
            new_ip = ip_var.get().strip() or DEFAULT_IP
            self.settings.set(new_ip, nt_var.get())
            ip2, nt2 = self.settings.get()
            save_config({"ip": ip2, "ntfy": nt2})
            self.baseline = None            # re-learn baseline for the (possibly new) miner
            self.ntfy_status = "READY" if nt2 else "OFF"
            return ip2, nt2

        def do_save():
            apply()
            w.destroy()

        def do_test_ntfy():
            _, nt2 = apply()
            msg.config(text="Sending test...", fg=shade(0.8))

            def done(ok, info):
                def upd():
                    if msg.winfo_exists():
                        msg.config(text=("ntfy OK (" + info + ")") if ok else ("ntfy FAILED: " + info[:60]),
                                   fg=shade(1) if ok else "#ff5050")
                self.ui(upd)
            send_ntfy(nt2, "Hex Radar test message", title="Hex Radar TEST", priority="default",
                      tags="satellite", on_done=done)

        tk.Button(w, text="TEST NTFY", command=do_test_ntfy, **btn).grid(row=5, column=0, pady=(14, 0), sticky="w")
        tk.Button(w, text="TEST PING", command=self.sound.play, **btn).grid(row=5, column=1, pady=(14, 0))
        tk.Button(w, text="SAVE", command=do_save, **btn).grid(row=5, column=2, pady=(14, 0), sticky="e")
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
                continue
            self.status = "LOCKED"
            self.hash, self.temp, self.power = d.get("hashRate"), d.get("temp"), d.get("power")
            v = parse_diff(d.get("bestSessionDiff"))
            if v is None:
                continue
            if self.baseline is None:
                self.baseline = v
            elif v > self.baseline:
                self.trigger(v)
                self.baseline = v
            elif v < self.baseline:        # session best never decreases -> miner restarted
                self.baseline = v
                self.status = "SESSION RESET"
            self.best = v

    def trigger(self, v, test=False):
        a = random.uniform(0, 2 * math.pi)
        r = random.uniform(0.25, 0.9)
        now = time.time()
        self.pings.append({"t": now, "a": a, "r": r})
        self.blips.append({"a": a, "deg": math.degrees(a) % 360, "r": r, "lit": 1.0,
                           "label": fmt_diff(v), "born": now})
        self.flash = 1.0
        self.sound.play()
        if test:
            return
        self.pings_total += 1
        ip, nt = self.settings.get()
        if nt:
            self.ntfy_status = "SENDING"

            def done(ok, info):
                self.ui(lambda: setattr(self, "ntfy_status", "SENT" if ok else "FAILED"))
            host = "DEMO" if self.args.demo else ip
            send_ntfy(nt, f"New session best: {fmt_diff(v)}\nMiner: {host}",
                      title="Hex Radar - NEW SESSION BEST", on_done=done)

    # ---- loop
    def tick(self):
        try:
            while True:
                try:
                    self.ui_q.get_nowait()()
                except queue.Empty:
                    break
                except Exception as e:
                    print("UI callback error:", e)
            now = time.time()
            dt = now - self.last
            self.last = now
            prev = self.angle
            self.angle = (self.angle + dt * self.rpm * 360 / 60) % 360
            self.poll()
            lo, hi = prev, self.angle
            for b in self.blips:
                deg = b["deg"]
                hit = (lo <= deg <= hi) if lo <= hi else (deg >= lo or deg <= hi)
                if hit:
                    b["lit"] = 1.0
                else:
                    b["lit"] = max(0.0, b["lit"] - dt / 6.0)
            self.blips = [b for b in self.blips if now - b["born"] < 600]
            self.pings = [p for p in self.pings if now - p["t"] < 3.5]
            self.flash = max(0.0, self.flash - dt * 1.5)
            self.draw()
        except Exception as e:
            print("tick error:", e)
        finally:
            try:
                self.root.after(FRAME_MS, self.tick)    # always reschedule
            except tk.TclError:
                pass                                    # window closed

    def draw_static(self, w, h, cx, cy, R):
        """Rings, spokes, ticks, bearing labels: drawn once per window size."""
        c = self.c
        c.delete("static")
        dim = shade(0.45)
        for k in range(1, 6):
            rr = R * k / 5
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=dim,
                          width=2 if k == 5 else 1, tags="static")
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            c.create_line(cx, cy, cx + R * math.sin(a), cy - R * math.cos(a), fill=shade(0.3), tags="static")
        for deg in range(0, 360, 5):
            a = math.radians(deg)
            ln = 14 if deg % 30 == 0 else 7
            c.create_line(cx + R * math.sin(a), cy - R * math.cos(a),
                          cx + (R - ln) * math.sin(a), cy - (R - ln) * math.cos(a), fill=dim, tags="static")
        for deg in range(0, 360, 30):
            a = math.radians(deg)
            c.create_text(cx + (R + 22) * math.sin(a), cy - (R + 22) * math.cos(a),
                          text=f"{deg:03d}", fill=shade(0.8), font=("Consolas", max(9, int(R / 40))),
                          tags="static")
        c.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=shade(1), outline="", tags="static")
        c.create_text(w - 20, h - 20, anchor="se",
                      text="F11 fullscreen | SPACE test ping | S settings | Q quit",
                      fill=shade(0.4), font=("Consolas", 10), tags="static")

    def draw(self):
        c = self.c
        w, h = c.winfo_width(), c.winfo_height()
        cx, cy = w / 2, h / 2
        R = min(w, h) / 2 - 40
        c.delete("dyn")
        if R < 50:
            return
        key = (w, h)
        if key != self.static_key:
            self.draw_static(w, h, cx, cy, R)
            self.static_key = key

        if self.flash > 0:
            c.create_rectangle(0, 0, w, h, fill=shade(0.12 * self.flash), outline="", tags=("dyn", "flash"))
        for i in range(SWEEP_STEPS):
            c.create_arc(cx - R, cy - R, cx + R, cy + R,
                         start=90 - (self.angle - SWEEP_SPAN * i / SWEEP_STEPS),
                         extent=SWEEP_SPAN / SWEEP_STEPS + 1.0, style="pieslice",
                         fill=shade(0.55 * (1 - i / SWEEP_STEPS) ** 2.2), outline="", tags=("dyn", "sweep"))
        c.tag_lower("sweep", "static")      # trail + flash sit beneath the grid
        if self.flash > 0:
            c.tag_lower("flash", "sweep")

        a = math.radians(self.angle)
        ex, ey = cx + R * math.sin(a), cy - R * math.cos(a)
        c.create_line(cx, cy, ex, ey, fill=shade(0.35), width=9, tags="dyn")
        c.create_line(cx, cy, ex, ey, fill=shade(1.0), width=2, tags="dyn")

        for b in self.blips:
            if b["lit"] <= 0:
                continue
            bx = cx + R * b["r"] * math.sin(b["a"])
            by = cy - R * b["r"] * math.cos(b["a"])
            s = 5 + 5 * b["lit"]
            c.create_oval(bx - s * 2, by - s * 2, bx + s * 2, by + s * 2, outline=shade(0.4 * b["lit"]), tags="dyn")
            c.create_oval(bx - s, by - s, bx + s, by + s, fill=shade(b["lit"]), outline="", tags="dyn")
            c.create_text(bx + s * 2 + 4, by - s * 2, anchor="w", text=b["label"], fill=shade(b["lit"]),
                          font=("Consolas", max(10, int(R / 34)), "bold"), tags="dyn")

        now = time.time()
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
                              outline=shade(max(0.0, 1 - t / 3.0)), width=3, tags="dyn")

        f = ("Consolas", max(10, int(R / 32)))
        fb = ("Consolas", max(14, int(R / 18)), "bold")
        c.create_text(20, 20, anchor="nw", text="HEX RADAR // SESSION BEST", fill=shade(1), font=fb, tags="dyn")
        hs = f"{self.hash:.0f} GH/s" if isinstance(self.hash, (int, float)) else "---"
        ts = f"{self.temp:.1f} C" if isinstance(self.temp, (int, float)) else "---"
        ps = f"{self.power:.1f} W" if isinstance(self.power, (int, float)) else "---"
        ip, nt = self.settings.get()
        lines = [f"TARGET {'DEMO' if self.args.demo else ip}",
                 f"STATUS {self.status}", f"BEST   {fmt_diff(self.best)}",
                 f"HASH   {hs}", f"ASIC   {ts}", f"POWER  {ps}", f"PINGS  {self.pings_total}",
                 f"NTFY   {self.ntfy_status if nt else 'OFF'}"]
        c.create_text(20, 20 + int(R / 12), anchor="nw", text="\n".join(lines),
                      fill=shade(0.85), font=f, tags="dyn")
        if self.flash > 0.4:
            c.create_text(cx, 40, text="*** NEW SESSION BEST ***", fill=shade(self.flash),
                          font=("Consolas", max(16, int(R / 14)), "bold"), tags="dyn")

def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default=None, help="Gamma Hex IP or hostname")
    ap.add_argument("--ntfy", default=None, help="ntfy topic or full URL")
    ap.add_argument("--interval", type=float, default=None, help="poll seconds")
    ap.add_argument("--rpm", type=float, default=None, help="sweep revolutions per minute")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    ip = args.ip or cfg.get("ip") or DEFAULT_IP
    nt = args.ntfy if args.ntfy is not None else cfg.get("ntfy", "")
    args.interval = args.interval if args.interval is not None else float(cfg.get("interval", DEFAULT_INTERVAL))
    args.rpm = args.rpm if args.rpm is not None else float(cfg.get("rpm", DEFAULT_RPM))
    if args.ip or args.ntfy is not None or "--interval" in sys.argv or "--rpm" in sys.argv:
        save_config({"ip": ip, "ntfy": nt, "interval": args.interval, "rpm": args.rpm})
    settings = Settings(ip, nt)
    root = tk.Tk()
    root.title("Hex Radar")
    root.geometry("1100x1000")
    root.configure(bg="black")
    Radar(root, args, settings)
    root.mainloop()

if __name__ == "__main__":
    main()
