
import re, tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

UNITS = {"mh/s": 1e-3, "gh/s": 1.0, "th/s": 1e3}
PAT = re.compile(r"(\d+(?:[.,]\d+)?)\s*(Mh/s|Gh/s|Th/s)", re.I)

def parse(text, domains=4):
    vals = [float(n.replace(",", "")) * UNITS[u.lower()] for n, u in PAT.findall(text)]
    if not vals:
        raise ValueError("No 'xxx Gh/s' values found.")
    if len(vals) % domains:
        raise ValueError(f"Found {len(vals)} values; not divisible by {domains} domains.")
    return np.array(vals).reshape(-1, domains)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Hashrate Register Heatmap")
        self.configure(bg="#10141c")
        self.snaps = []
        left = tk.Frame(self, bg="#10141c"); left.pack(side="left", fill="y", padx=8, pady=8)
        tk.Label(left, text="Paste Hashrate Registers:", fg="#d4af37", bg="#10141c").pack(anchor="w")
        self.txt = tk.Text(left, width=34, height=28, bg="#1b2230", fg="#e8e8e8", insertbackground="white")
        self.txt.pack()
        row = tk.Frame(left, bg="#10141c"); row.pack(fill="x", pady=4)
        tk.Label(row, text="Domains/ASIC", fg="white", bg="#10141c").pack(side="left")
        self.dom = tk.IntVar(value=4)
        tk.Spinbox(row, from_=1, to=16, width=4, textvariable=self.dom).pack(side="left", padx=4)
        self.mode = tk.StringVar(value="Average")
        ttk.Combobox(row, textvariable=self.mode, width=9, state="readonly",
                     values=["Average", "Latest"]).pack(side="left")
        self.mode.trace_add("write", lambda *a: self.draw())
        b = tk.Frame(left, bg="#10141c"); b.pack(fill="x")
        ttk.Button(b, text="Add snapshot", command=self.add).pack(side="left", padx=2)
        ttk.Button(b, text="Clear all", command=self.clear).pack(side="left", padx=2)
        ttk.Button(b, text="Save PNG", command=self.save).pack(side="left", padx=2)
        self.info = tk.Label(left, text="Snapshots: 0", fg="#9fb3c8", bg="#10141c"); self.info.pack(anchor="w")
        self.fig = Figure(figsize=(8, 6), facecolor="#10141c")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(side="right", fill="both", expand=True)
        self.draw()

    def add(self):
        try:
            a = parse(self.txt.get("1.0", "end"), self.dom.get())
            if self.snaps and a.shape != self.snaps[0].shape:
                raise ValueError("Shape differs from earlier snapshots. Clear first.")
        except ValueError as e:
            messagebox.showerror("Parse error", str(e)); return
        self.snaps.append(a); self.txt.delete("1.0", "end"); self.draw()

    def clear(self):
        self.snaps = []; self.draw()

    def save(self):
        self.fig.savefig("heatmap.png", dpi=150, facecolor=self.fig.get_facecolor())
        self.info.config(text="Saved heatmap.png")

    def draw(self):
        self.fig.clear()
        if not self.snaps:
            self.canvas.draw(); return
        data = np.mean(self.snaps, axis=0) if self.mode.get() == "Average" else self.snaps[-1]
        mean = data.mean()
        dev = (data / mean - 1) * 100
        ax = self.fig.add_axes([0.08, 0.1, 0.62, 0.8], facecolor="#10141c")
        lim = max(10, np.abs(dev).max())
        im = ax.imshow(dev, cmap="RdYlGn", vmin=-lim, vmax=lim, aspect="auto")
        for (i, j), v in np.ndenumerate(data):
            ax.text(j, i, f"{v:.0f}\n{dev[i,j]:+.1f}%", ha="center", va="center", fontsize=9, color="black")
        ax.set_xticks(range(data.shape[1])); ax.set_xticklabels([f"D{j+1}" for j in range(data.shape[1])])
        ax.set_yticks(range(data.shape[0])); ax.set_yticklabels([f"ASIC {i+1}" for i in range(data.shape[0])])
        ax.tick_params(colors="white")
        ax.set_title(f"{self.mode.get()} of {len(self.snaps)} snapshot(s) | domain mean {mean:.0f} Gh/s | total {data.sum()/1000:.2f} Th/s", color="#d4af37", fontsize=10)
        ax2 = self.fig.add_axes([0.78, 0.1, 0.19, 0.8], facecolor="#10141c")
        tot = data.sum(axis=1)
        cols = ["#c0392b" if t < tot.mean()*0.95 else "#27ae60" if t > tot.mean()*1.05 else "#d4af37" for t in tot]
        ax2.barh(range(len(tot)), tot, color=cols); ax2.invert_yaxis()
        ax2.axvline(tot.mean(), color="white", ls="--", lw=1)
        ax2.set_yticks([]); ax2.tick_params(colors="white"); ax2.set_xlabel("Gh/s per ASIC", color="white")
        for s in ax2.spines.values(): s.set_color("#445")
        self.info.config(text=f"Snapshots: {len(self.snaps)}")
        self.canvas.draw()

if __name__ == "__main__":
    App().mainloop()
