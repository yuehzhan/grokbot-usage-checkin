import csv, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
for f in font_manager.findSystemFonts():
    if "NotoSansCJK" in f.replace(" ", "") or "NotoSerifCJK" in f.replace(" ", ""):
        font_manager.fontManager.addfont(f)
plt.rcParams["font.family"] = ["Noto Sans CJK TC", "Noto Serif CJK TC", "DejaVu Sans"]
PACE = 14  # % per day (grey baseline)
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usage_palette as _pal
_P = _pal.get(os.environ.get("USAGE_PALETTE"))  # orange = over, cool colour = under, grey = baseline
GREEN, PURPLE, ORANGE = _P["under"], _P["over"], _P["baseline"]  # names kept: GREEN=under, PURPLE=over, ORANGE=baseline
plt.style.use("dark_background")
src = sys.argv[1] if len(sys.argv) > 1 else "usage.csv"
rows = list(csv.DictReader(open(src, encoding="utf-8")))
wstart = sys.argv[3] if len(sys.argv) > 3 else rows[0]["date"]
days = [int(r["day"]) for r in rows]; pct = [float(r["pct"]) for r in rows]
fig, ax = plt.subplots(figsize=(8, 4.8), dpi=110)
ax.plot([0, 7], [0, PACE * 7], color=ORANGE, lw=2.8, label=f"基準線 每天 {PACE}%")
ax.axhline(100, color="#bbbbbb", lw=1, ls="--")
px, py = [0] + days, [0] + pct
for i in range(1, len(px)):
    c = PURPLE if py[i] > PACE * px[i] else GREEN
    ax.plot(px[i-1:i+1], py[i-1:i+1], color=c, lw=3.6)
for d, p in zip(days, pct):
    over = p - PACE * d
    c = PURPLE if over > 0 else GREEN
    ax.scatter([d], [p], color=c, s=55, zorder=3)
    ax.annotate(f"{p:.0f}%\n({'▲' if over > 0 else '▼'}{over:+.0f})", (d, p), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=10, color=c, fontweight="bold")
ax.set_xlim(0, 7.3); ax.set_ylim(0, 110); ax.set_xticks(range(0, 8))
ax.set_xticklabels(["重置"] + [f"第{d}天" for d in range(1, 8)])
ax.set_ylabel("每週用量 %")
ax.set_title(f"本週用量（{wstart} 起）")
from matplotlib.lines import Line2D
ax.legend(handles=[Line2D([0],[0],color=ORANGE,lw=2.8,label=f"基準線 每天 {PACE}%"),
    Line2D([0],[0],color=GREEN,lw=3.6,label="▼ 低於基準"), Line2D([0],[0],color=PURPLE,lw=3.6,label="▲ 高於基準")], loc="upper left")
ax.grid(color="#444444", alpha=0.6)
fig.patch.set_facecolor("black"); ax.set_facecolor("black")
fig.tight_layout(); fig.savefig(sys.argv[2] if len(sys.argv) > 2 else "usage-week.png")
