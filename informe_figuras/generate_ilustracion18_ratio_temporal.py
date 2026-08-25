#!/usr/bin/env python3
"""
Ilustración 18 — serie temporal del pupil_iris_ratio de una sesión real,
para ambos ojos.

Datos: testing/model_eval/<sesion>_left/frames.csv y .../_right/frames.csv,
generados por testing/local_test_inference.py sobre un video real ya
procesado por el pipeline de inferencia (functions/inference/yolo_onnx.py).

Las bandas de "pantalla blanca" se toman de la línea de tiempo del estímulo
pupila_360 (Test final/video/pupila_360_metricas.md): 6 ciclos de luz de 4 s
tras 30 s de adaptación a la oscuridad. Se alinean al cierre de la sesión
(igual que las series de ambos ojos). Opcionalmente --events agrega marcas
extra (csv timestamp_ms,label).

Uso:
    python informe_figuras/generate_ilustracion18_ratio_temporal.py
    python informe_figuras/generate_ilustracion18_ratio_temporal.py --session <nombre_base>
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "testing" / "model_eval"
OUTPUT_DIR = REPO_ROOT / "informe_figuras"

DEFAULT_SESSION = "f09c929a-96a7-45f5-8f9f-bcd7412a7a0f"

DPI = 200
WIDTH_CM = 16.0
WIDTH_IN = WIDTH_CM / 2.54

COLOR_LEFT = "#5B7A99"    # azul-gris — ojo izquierdo
COLOR_RIGHT = "#C1584C"   # terracota — ojo derecho
COLOR_LIGHT = "#F2E6A0"   # amarillo suave — fase de pantalla blanca
COLOR_POST_LIGHT = "#8A8040"  # borde de latencia post-luz
CONF_THRESHOLD = 0.3      # bajo esto se considera parpadeo/oclusión no confiable
SMOOTH_WINDOW = 9         # ancho (frames) del promedio móvil aplicado al ratio

# Estímulo pupila_360: duración total y fases de luz (inicio, fin) en segundos
# del video. Fuente: Test final/video/pupila_360_metricas.md
VIDEO_DURATION_S = 102.0
# Desfase empírico respecto al timestamp nominal del video (fade + sync).
# Valor negativo = las bandas se dibujan antes en el eje de la sesión.
LIGHT_ONSET_OFFSET_S = -3.5
# Ventana tras el apagado en la que la pupila puede seguir contrayéndose.
POST_LIGHT_CONSTRICTION_S = 0.7
LIGHT_PHASES_S = (
    (30.0, 34.0),  # ciclo 1
    (42.0, 46.0),  # ciclo 2
    (54.0, 58.0),  # ciclo 3
    (66.0, 70.0),  # ciclo 4
    (78.0, 82.0),  # ciclo 5
    (90.0, 94.0),  # ciclo 6
)

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "axes.edgecolor": "#444444",
    "axes.linewidth": 0.8,
    "savefig.bbox": "tight",
})


def load_ratio_series(frames_csv: Path) -> tuple[np.ndarray, np.ndarray]:
    """timestamp (s) y pupil_iris_ratio; NaN donde la confianza es baja (parpadeo/oclusión)."""
    t, ratio = [], []
    with frames_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            t.append(float(row["timestamp_ms"]) / 1000.0)
            pconf, iconf = float(row["pupil_confidence"]), float(row["iris_confidence"])
            r = float(row["pupil_iris_ratio"])
            ratio.append(r if (pconf >= CONF_THRESHOLD and iconf >= CONF_THRESHOLD and r > 0) else np.nan)
    return np.array(t), np.array(ratio)


def smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Promedio móvil centrado que ignora NaN (parpadeo/oclusión) sin propagarlos
    más allá de su propia ventana."""
    n = len(values)
    out = np.full(n, np.nan)
    half = window // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        chunk = values[lo:hi]
        valid = chunk[~np.isnan(chunk)]
        if valid.size:
            out[i] = valid.mean()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--session", default=DEFAULT_SESSION,
                         help="Nombre base de la sesión (busca <session>_left/ y <session>_right/ en testing/model_eval/)")
    parser.add_argument("--events", default=None,
                         help="CSV opcional con columnas timestamp_ms,label para marcar eventos de la tarea de RV")
    args = parser.parse_args()

    left_csv = SESSIONS_DIR / f"{args.session}_left" / "frames.csv"
    right_csv = SESSIONS_DIR / f"{args.session}_right" / "frames.csv"
    if not left_csv.exists() or not right_csv.exists():
        raise SystemExit(f"No se encontraron frames.csv para la sesión {args.session!r} en {SESSIONS_DIR}")

    t_left, r_left = load_ratio_series(left_csv)
    t_right, r_right = load_ratio_series(right_csv)
    r_left = smooth(r_left, SMOOTH_WINDOW)
    r_right = smooth(r_right, SMOOTH_WINDOW)

    # Las dos grabaciones no arrancaron perfectamente sincronizadas, pero
    # ambas terminan junto con el cierre de la sesión: se alinean por el final
    # y luego se desplaza el eje para que el gráfico arranque en 0s.
    right_duration = t_right[-1]
    t_left = t_left - t_left[-1]
    t_right = t_right - right_duration
    offset = -min(t_left[0], t_right[0])
    t_left = t_left + offset
    t_right = t_right + offset

    fig, ax = plt.subplots(figsize=(WIDTH_IN, WIDTH_IN * 0.45))

    # Video y grabaciones terminan con el cierre de sesión: mapear tiempos del
    # estímulo pupila_360 al eje ya alineado por el final.
    session_end = max(t_left[-1], t_right[-1])

    def video_to_plot(t_video: float) -> float:
        return session_end - (VIDEO_DURATION_S - t_video)

    for i, (t0, t1) in enumerate(LIGHT_PHASES_S):
        x0 = video_to_plot(t0 + LIGHT_ONSET_OFFSET_S)
        x1 = video_to_plot(t1 + LIGHT_ONSET_OFFSET_S)
        x_post = x1 + POST_LIGHT_CONSTRICTION_S
        ax.axvspan(x0, x1, color=COLOR_LIGHT, alpha=0.55, lw=0, zorder=0)
        ax.axvline(x0, color="#B8A84A", ls="-", lw=0.7, alpha=0.85, zorder=1)
        ax.axvline(x1, color="#B8A84A", ls="-", lw=0.7, alpha=0.85, zorder=1)
        ax.axvline(x_post, color=COLOR_POST_LIGHT, ls=":", lw=1.0, alpha=0.9, zorder=2)
        if i == 0:
            ax.text((x0 + x1) / 2, 0.98, "", transform=ax.get_xaxis_transform(),
                    ha="center", va="top", fontsize=7, color="#7A7030")

    ax.plot(t_left, r_left, color=COLOR_LEFT, lw=1.1, label="Ojo izquierdo", zorder=3)
    ax.plot(t_right, r_right, color=COLOR_RIGHT, lw=1.1, label="Ojo derecho", zorder=3)

    if args.events:
        events_path = Path(args.events)
        with events_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                # timestamp_ms se asume relativo al inicio de la grabación del
                # ojo derecho; se alinea igual que su serie, por el final.
                ts = float(row["timestamp_ms"]) / 1000.0 - right_duration + offset
                ax.axvline(ts, color="#888888", ls="--", lw=0.8, alpha=0.7, zorder=2)
                ax.text(ts, ax.get_ylim()[1], row["label"], rotation=90,
                        fontsize=7, ha="right", va="top", color="#666666")

    ax.set_xlabel("Tiempo (s)")
    ax.set_ylabel("pupil_iris_ratio")
    ax.set_title("Serie temporal del pupil_iris_ratio — sesión real (ambos ojos)", pad=28)
    handles, labels = ax.get_legend_handles_labels()
    handles.append(Patch(facecolor=COLOR_LIGHT, edgecolor="#B8A84A",
                         alpha=0.55, label="Pantalla blanca"))
    handles.append(plt.Line2D([0], [0], color=COLOR_POST_LIGHT, ls=":", lw=1.2,
                              label="Posible latencia constricción rezagada"))
    ax.legend(handles=handles, frameon=False, loc="lower right",
              bbox_to_anchor=(1.0, 1.02), ncol=4, fontsize=8,
              borderaxespad=0, columnspacing=1.2)


    ax.set_axisbelow(True)
    ax.grid(color="#dddddd", linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    note = (
        f"Sesión: {args.session} · huecos = parpadeo/oclusión (confianza < {CONF_THRESHOLD}). "
        "Bandas = fases de pantalla blanca del estímulo pupila_360 "
        "(4 s luz / 8 s oscuridad × 6 ciclos, tras 30 s de adaptación). "
        "Línea punteada = +0,7 s tras el apagado (la pupila puede seguir contrayéndose)."
    )
    fig.text(0.5, -0.06, note, ha="center", fontsize=8, color="#777777", wrap=True)

    fig.tight_layout()
    out = OUTPUT_DIR / "ilustracion18_pupil_iris_ratio_sesion_real.png"
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    print(f"guardado: {out}")


if __name__ == "__main__":
    main()
