"""
Fig. 3 -- Rendimiento de SegmentFrames (Map): fps por cantidad de frames y concurrencia.
Datos reales medidos via CloudWatch/Step Functions, sa-east-1.
"""
import matplotlib.pyplot as plt
import numpy as np

labels = ["10", "100", "1.000", "2.774", "10.000", "36.000"]
c10 = [3.5, 32.1, 80.6, 83.1, 81.0, 81.2]
c1000 = [5.2, 35.6, 76.3, 211.7, 240.3, 302.8]

COLOR_C10 = "#2a78d6"
COLOR_C1000 = "#eb6834"
TEXT_PRIMARY = "#0b0b0b"
TEXT_MUTED = "#6b6a66"
GRID = "#e1e0d9"

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Arial", "Helvetica", "DejaVu Sans"]

x = np.arange(len(labels))
bar_w = 0.32

fig, ax = plt.subplots(figsize=(9.5, 5.5), dpi=300)
fig.patch.set_facecolor("white")
ax.set_facecolor("white")

bars_c10 = ax.bar(x - bar_w / 2, c10, width=bar_w, color=COLOR_C10,
                   label="Concurrencia = 10", zorder=3)
bars_c1000 = ax.bar(x + bar_w / 2, c1000, width=bar_w, color=COLOR_C1000,
                     label="Concurrencia = 1.000", zorder=3)

ax.plot(x - bar_w / 2, c10, color=COLOR_C10, linewidth=1.8,
        marker="o", markersize=4.5, markerfacecolor=COLOR_C10,
        markeredgecolor="white", markeredgewidth=1, zorder=4)
ax.plot(x + bar_w / 2, c1000, color=COLOR_C1000, linewidth=1.8,
        marker="o", markersize=4.5, markerfacecolor=COLOR_C1000,
        markeredgecolor="white", markeredgewidth=1, zorder=4)

# Etiquetas de valor: si las dos barras quedan muy parejas, se separan
# horizontalmente (una a cada lado) en vez de superponerse verticalmente.
for i, (v10, v1000) in enumerate(zip(c10, c1000)):
    close = abs(v10 - v1000) < 8
    if close:
        ax.annotate(f"{v10:.1f}", (x[i] - bar_w / 2, v10),
                    textcoords="offset points", xytext=(-9, 8),
                    ha="right", fontsize=9, color=TEXT_PRIMARY)
        ax.annotate(f"{v1000:.1f}", (x[i] + bar_w / 2, v1000),
                    textcoords="offset points", xytext=(9, 8),
                    ha="left", fontsize=9, color=TEXT_PRIMARY)
    else:
        ax.annotate(f"{v10:.1f}", (x[i] - bar_w / 2, v10),
                    textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=9, color=TEXT_PRIMARY)
        ax.annotate(f"{v1000:.1f}", (x[i] + bar_w / 2, v1000),
                    textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=9, color=TEXT_PRIMARY)

ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=10.5, color=TEXT_PRIMARY)
ax.set_xlabel("Cantidad de frames del video", fontsize=11, color=TEXT_PRIMARY, labelpad=10)
ax.set_ylabel("fps", fontsize=11, color=TEXT_PRIMARY)
ax.set_ylim(0, 330)

ax.yaxis.grid(True, color=GRID, linewidth=0.8, zorder=0)
ax.set_axisbelow(True)
for spine in ["top", "right", "left"]:
    ax.spines[spine].set_visible(False)
ax.spines["bottom"].set_color("#c3c2b7")
ax.tick_params(axis="y", colors=TEXT_MUTED, labelsize=9.5)
ax.tick_params(axis="x", length=0)

fig.suptitle("Rendimiento de SegmentFrames (Map): fps por cantidad de frames y concurrencia",
             x=0.01, y=0.995, ha="left", fontsize=12.5, fontweight="bold", color=TEXT_PRIMARY)

ax.legend(loc="lower center", frameon=False, fontsize=10, ncol=2,
          bbox_to_anchor=(0.5, 1.02))

fig.text(0.01, 0.005,
         "Datos reales medidos vía CloudWatch/Step Functions (sa-east-1). "
         "Eje X espaciado por categoría, no a escala lineal.",
         fontsize=8, color=TEXT_MUTED)

fig.tight_layout(rect=(0, 0.03, 1, 0.90))

fig.savefig("fig3_rendimiento_pipeline.png", dpi=300, facecolor="white", bbox_inches="tight")
fig.savefig("fig3_rendimiento_pipeline.pdf", facecolor="white", bbox_inches="tight")
print("OK: fig3_rendimiento_pipeline.png / .pdf generados")
