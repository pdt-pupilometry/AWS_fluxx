"""
Evaluación local del modelo ONNX sobre un video completo, sin AWS.

Replica el preprocesamiento exacto del pipeline (escala de grises → resize
480x640 → JPEG q90) y corre la misma inferencia de la Lambda 2
(functions/inference/yolo_onnx.py) frame a frame. Produce:

  - annotated.mp4     video con las elipses predichas dibujadas (pupila e iris)
  - frames.csv        métricas por frame (las 5 del pipeline + geometría + tiempos)
  - summary.json      estadísticas agregadas (tasas de detección, áreas, latencia)
  - charts/*.png      gráficos: áreas, ratio, confianzas, latencia, distribución

Por defecto, la elipse de iris se fuerza a ser concéntrica con la de pupila
(mismo centro, mismo ángulo, misma relación de aspecto), solo que más grande
-- el tamaño se deriva del área de iris detectada de forma independiente, así
el área/ratio reportado no cambia, solo la geometría dibujada y sus
coordenadas. Desactivar con --iris-independent.

Uso:
    python scripts/evaluate_model.py --video ~/Documents/Tesis/video/pupila_360.mp4
    python scripts/evaluate_model.py --video video.mp4 --max-frames 200
    python scripts/evaluate_model.py --video video.mp4 --no-preproc   # resolución original
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = REPO_ROOT / "functions" / "inference" / "model" / "yolo26l_seg.onnx"

# Mismos parámetros que el pipeline (template.yaml: TargetWidth/TargetHeight/JpegQuality)
TARGET_WIDTH = 480
TARGET_HEIGHT = 640
JPEG_QUALITY = 90

# Colores BGR para el overlay (paleta categórica slots 1 y 4: azul / amarillo)
COLOR_PUPIL = (229, 135, 57)   # #3987e5
COLOR_IRIS = (0, 161, 237)     # #eda100
COLOR_TEXT = (255, 255, 255)

# Paleta para los gráficos (modo claro validado; slots adyacentes 1-2 = azul/verde)
CHART_SERIES_1 = "#2a78d6"
CHART_SERIES_2 = "#008300"
CHART_SURFACE = "#fcfcfb"
CHART_GRID = "#e1e0d9"
CHART_AXIS = "#c3c2b7"
CHART_MUTED = "#898781"
CHART_INK = "#0b0b0b"


def fit_ellipse_from_mask(mask: np.ndarray | None):
    """
    Igual que yolo_onnx.ellipse_area_from_mask pero devolviendo la elipse
    completa para poder dibujarla: ((cx, cy), (MA, ma), angle) o None.
    """
    if mask is None:
        return None
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if len(contour) < 5:
        return None
    try:
        (cx, cy), (major_axis, minor_axis), angle = cv2.fitEllipseDirect(contour)
    except cv2.error:
        return None
    # fitEllipseDirect resuelve la cónica sin restringirla a una elipse: en
    # contornos casi degenerados (parpadeo) puede devolver NaN/inf, que revienta
    # cv2.ellipse al dibujar. yolo_onnx no lo nota porque solo calcula un área.
    if not all(math.isfinite(v) for v in (cx, cy, major_axis, minor_axis, angle)):
        return None
    return (cx, cy), (major_axis, minor_axis), angle


def ellipse_area(ellipse) -> float:
    if ellipse is None:
        return 0.0
    (_, _), (major_axis, minor_axis), _ = ellipse
    return float(math.pi * major_axis * minor_axis / 4.0)


def constrain_iris_to_pupil(pupil_ellipse, iris_ellipse_raw):
    """
    Fuerza la elipse de iris a compartir centro y forma (relación de aspecto
    y orientación) con la de pupila -- concéntricas, mismo ángulo, solo que
    más grande. El tamaño se deriva del área detectada de forma independiente
    para el iris, así el área/ratio calculado no cambia, solo la geometría.

    Si falta alguna de las dos detecciones no hay referencia para concentrar
    (no hay centro de pupila, o no hay tamaño de iris), y se devuelve la
    elipse de iris cruda tal cual se detectó.
    """
    if pupil_ellipse is None or iris_ellipse_raw is None:
        return iris_ellipse_raw

    pupil_area = ellipse_area(pupil_ellipse)
    iris_area = ellipse_area(iris_ellipse_raw)
    if pupil_area <= 0 or iris_area <= 0:
        return iris_ellipse_raw

    scale = math.sqrt(iris_area / pupil_area)
    (cx, cy), (major_axis, minor_axis), angle = pupil_ellipse
    return (cx, cy), (major_axis * scale, minor_axis * scale), angle


def draw_overlay(frame_bgr: np.ndarray, pupil_ellipse, iris_ellipse, record: dict) -> np.ndarray:
    out = frame_bgr.copy()
    if iris_ellipse is not None:
        cv2.ellipse(out, iris_ellipse, COLOR_IRIS, 2)
        cv2.circle(out, (round(iris_ellipse[0][0]), round(iris_ellipse[0][1])), 2, COLOR_IRIS, -1)
    if pupil_ellipse is not None:
        cv2.ellipse(out, pupil_ellipse, COLOR_PUPIL, 2)
        cv2.circle(out, (round(pupil_ellipse[0][0]), round(pupil_ellipse[0][1])), 2, COLOR_PUPIL, -1)

    lines = [
        f"frame {record['frame_index']}  t={record['timestamp_ms']}ms",
        f"pupil  conf={record['pupil_confidence']:.2f}  area={record['pupil_area_pixels']:.0f}px",
        f"iris   conf={record['iris_confidence']:.2f}  area={record['iris_area_pixels']:.0f}px",
        f"ratio  {record['pupil_iris_ratio']:.4f}",
    ]
    for i, text in enumerate(lines):
        y = 20 + 18 * i
        cv2.putText(out, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(out, text, (8, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, COLOR_TEXT, 1, cv2.LINE_AA)
    return out


def new_axes(plt, title: str, xlabel: str, ylabel: str):
    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=150)
    fig.patch.set_facecolor(CHART_SURFACE)
    ax.set_facecolor(CHART_SURFACE)
    ax.set_title(title, color=CHART_INK, fontsize=12, loc="left", pad=12)
    ax.set_xlabel(xlabel, color=CHART_MUTED, fontsize=9)
    ax.set_ylabel(ylabel, color=CHART_MUTED, fontsize=9)
    ax.grid(axis="y", color=CHART_GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(CHART_AXIS)
    ax.tick_params(colors=CHART_MUTED, labelsize=8)
    return fig, ax


def save_fig(fig, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, facecolor=CHART_SURFACE)
    print(f"  gráfico: {path}")


def make_charts(records: list[dict], out_dir: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    charts_dir = out_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    t = [r["timestamp_ms"] / 1000.0 for r in records]

    # 1. Áreas de pupila e iris en el tiempo
    fig, ax = new_axes(plt, "Área de pupila e iris por frame", "tiempo (s)", "área (px²)")
    ax.plot(t, [r["pupil_area_pixels"] for r in records], color=CHART_SERIES_1, linewidth=1.4, label="Pupila")
    ax.plot(t, [r["iris_area_pixels"] for r in records], color=CHART_SERIES_2, linewidth=1.4, label="Iris")
    ax.legend(frameon=False, fontsize=9, labelcolor=CHART_INK)
    save_fig(fig, charts_dir / "areas_por_frame.png")

    # 2. Ratio pupila/iris en el tiempo (serie única, sin leyenda)
    fig, ax = new_axes(plt, "Ratio pupila/iris por frame", "tiempo (s)", "ratio")
    ax.plot(t, [r["pupil_iris_ratio"] for r in records], color=CHART_SERIES_1, linewidth=1.4)
    save_fig(fig, charts_dir / "ratio_por_frame.png")

    # 3. Confianzas del modelo en el tiempo
    fig, ax = new_axes(plt, "Confianza del modelo por frame", "tiempo (s)", "confianza")
    ax.plot(t, [r["pupil_confidence"] for r in records], color=CHART_SERIES_1, linewidth=1.4, label="Pupila")
    ax.plot(t, [r["iris_confidence"] for r in records], color=CHART_SERIES_2, linewidth=1.4, label="Iris")
    ax.set_ylim(0, 1.05)
    ax.legend(frameon=False, fontsize=9, labelcolor=CHART_INK)
    save_fig(fig, charts_dir / "confianza_por_frame.png")

    # 4. Latencia de inferencia por frame
    fig, ax = new_axes(plt, "Latencia de inferencia por frame", "tiempo del video (s)", "latencia (ms)")
    ax.plot(t, [r["inference_ms"] for r in records], color=CHART_SERIES_1, linewidth=1.4)
    save_fig(fig, charts_dir / "latencia_por_frame.png")

    # 5. Distribución del ratio (solo frames con ambas detecciones)
    ratios = [r["pupil_iris_ratio"] for r in records if r["pupil_iris_ratio"] > 0]
    if ratios:
        fig, ax = new_axes(plt, "Distribución del ratio pupila/iris", "ratio", "frames")
        ax.hist(ratios, bins=40, color=CHART_SERIES_1, edgecolor=CHART_SURFACE, linewidth=0.8)
        save_fig(fig, charts_dir / "distribucion_ratio.png")


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = math.floor(k)
    c = min(f + 1, len(s) - 1)
    return s[f] + (s[c] - s[f]) * (k - f)


def build_summary(records: list[dict], video_path: str, model_path: str, fps: float, wall_seconds: float) -> dict:
    n = len(records)
    pupil_ok = [r for r in records if r["pupil_confidence"] > 0]
    iris_ok = [r for r in records if r["iris_confidence"] > 0]
    both_ok = [r for r in records if r["pupil_iris_ratio"] > 0]
    lat = [r["inference_ms"] for r in records]

    def stats(values: list[float]) -> dict:
        if not values:
            return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0, "std": 0.0}
        return {
            "mean": round(statistics.fmean(values), 4),
            "median": round(statistics.median(values), 4),
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "std": round(statistics.pstdev(values), 4) if len(values) > 1 else 0.0,
        }

    return {
        "video": video_path,
        "model": model_path,
        "total_frames": n,
        "video_fps": round(fps, 2),
        "detection_rate": {
            "pupil": round(len(pupil_ok) / n, 4) if n else 0.0,
            "iris": round(len(iris_ok) / n, 4) if n else 0.0,
            "both": round(len(both_ok) / n, 4) if n else 0.0,
        },
        "frames_without_pupil": n - len(pupil_ok),
        "frames_without_iris": n - len(iris_ok),
        "pupil_area_pixels": stats([r["pupil_area_pixels"] for r in pupil_ok]),
        "iris_area_pixels": stats([r["iris_area_pixels"] for r in iris_ok]),
        "pupil_iris_ratio": stats([r["pupil_iris_ratio"] for r in both_ok]),
        "pupil_confidence": stats([r["pupil_confidence"] for r in pupil_ok]),
        "iris_confidence": stats([r["iris_confidence"] for r in iris_ok]),
        "inference_ms": {
            **stats(lat),
            "p95": round(percentile(lat, 95), 2),
            "throughput_fps": round(n / wall_seconds, 2) if wall_seconds > 0 else 0.0,
        },
        "wall_time_seconds": round(wall_seconds, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", required=True, help="Video de entrada (.mp4)")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Ruta al modelo ONNX")
    parser.add_argument("--conf-threshold", default="0.25", help="Umbral de confianza (igual que en AWS)")
    parser.add_argument("--output-dir", default=None, help="Directorio de salida (default: testing/model_eval/<video>)")
    parser.add_argument("--max-frames", type=int, default=0, help="Procesar solo los primeros N frames (0 = todos)")
    parser.add_argument("--no-preproc", action="store_true",
                        help="No replicar el preprocesamiento del pipeline (usa la resolución original a color)")
    parser.add_argument("--no-video", action="store_true", help="No generar el video anotado")
    parser.add_argument(
        "--rotate180",
        action="store_true",
        help="Rotar cada frame 180 grados antes de procesar (cámara montada al revés)",
    )
    parser.add_argument(
        "--iris-independent",
        action="store_true",
        help="No forzar que la elipse de iris comparta centro/forma con la de pupila "
        "(por defecto se fuerza: concéntrica, mismo ángulo y relación de aspecto, escalada)",
    )
    args = parser.parse_args()

    model_path = Path(args.model).resolve()
    if not model_path.exists():
        raise SystemExit(f"No existe el modelo: {model_path}")
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise SystemExit(f"No existe el video: {video_path}")

    # El módulo de la Lambda lee MODEL_PATH/CONF_THRESHOLD al importarse
    os.environ["MODEL_PATH"] = str(model_path)
    os.environ["CONF_THRESHOLD"] = args.conf_threshold
    sys.path.insert(0, str(REPO_ROOT / "functions" / "inference"))
    from yolo_onnx import (  # noqa: E402
        best_detections_per_class,
        build_mask,
        compute_pupil_iris_ratio,
        letterbox,
        _get_session,
    )

    out_dir = Path(args.output_dir) if args.output_dir else REPO_ROOT / "testing" / "model_eval" / video_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise SystemExit(f"No se pudo abrir el video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if args.max_frames:
        total = min(total, args.max_frames)
    print(f"Video: {video_path.name}  ({total} frames @ {fps:.1f} fps)")
    print(f"Modelo: {model_path.name}  (conf >= {args.conf_threshold})")
    print(f"Salida: {out_dir}")

    print("Cargando sesión ONNX...")
    t0 = time.perf_counter()
    _get_session()
    print(f"  modelo cargado en {time.perf_counter() - t0:.1f}s")

    writer = None
    records: list[dict] = []
    start = time.perf_counter()

    frame_index = 0
    while True:
        if args.max_frames and frame_index >= args.max_frames:
            break
        ok, frame = cap.read()
        if not ok:
            break
        if args.rotate180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        ts_ms = int(round(frame_index * 1000.0 / fps))

        if args.no_preproc:
            img_gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            # Preprocesamiento idéntico a la Lambda 1: gris → 480x640 → JPEG q90
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (TARGET_WIDTH, TARGET_HEIGHT), interpolation=cv2.INTER_AREA)
            _, jpeg = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            img_gray = cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE)

        # Inferencia — mismos pasos que yolo_onnx.infer_frame_metrics, pero
        # conservando las máscaras para poder dibujar las elipses completas
        t_inf = time.perf_counter()
        img_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
        canvas, ratio, pad_left, pad_top = letterbox(img_bgr)
        blob = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        session, input_name = _get_session()
        outputs = session.run(None, {input_name: blob})
        pupil_det, iris_det, protos = best_detections_per_class(outputs)

        orig_shape = img_gray.shape[:2]
        pupil_mask = build_mask(pupil_det, protos, ratio, pad_left, pad_top, orig_shape)
        iris_mask = build_mask(iris_det, protos, ratio, pad_left, pad_top, orig_shape)
        pupil_ellipse = fit_ellipse_from_mask(pupil_mask)
        iris_ellipse = fit_ellipse_from_mask(iris_mask)
        if not args.iris_independent:
            iris_ellipse = constrain_iris_to_pupil(pupil_ellipse, iris_ellipse)
        inference_ms = (time.perf_counter() - t_inf) * 1000.0

        pupil_area = ellipse_area(pupil_ellipse)
        iris_area = ellipse_area(iris_ellipse)
        record = {
            "frame_index": frame_index,
            "timestamp_ms": ts_ms,
            "pupil_area_pixels": round(pupil_area, 2),
            "iris_area_pixels": round(iris_area, 2),
            "pupil_iris_ratio": compute_pupil_iris_ratio(pupil_area, iris_area),
            "pupil_confidence": round(pupil_det["confidence"], 4) if pupil_det else 0.0,
            "iris_confidence": round(iris_det["confidence"], 4) if iris_det else 0.0,
            "pupil_cx": round(pupil_ellipse[0][0], 1) if pupil_ellipse else 0.0,
            "pupil_cy": round(pupil_ellipse[0][1], 1) if pupil_ellipse else 0.0,
            "pupil_major_axis": round(pupil_ellipse[1][0], 1) if pupil_ellipse else 0.0,
            "pupil_minor_axis": round(pupil_ellipse[1][1], 1) if pupil_ellipse else 0.0,
            "iris_cx": round(iris_ellipse[0][0], 1) if iris_ellipse else 0.0,
            "iris_cy": round(iris_ellipse[0][1], 1) if iris_ellipse else 0.0,
            "iris_major_axis": round(iris_ellipse[1][0], 1) if iris_ellipse else 0.0,
            "iris_minor_axis": round(iris_ellipse[1][1], 1) if iris_ellipse else 0.0,
            "inference_ms": round(inference_ms, 1),
        }
        records.append(record)

        if not args.no_video:
            annotated = draw_overlay(img_bgr, pupil_ellipse, iris_ellipse, record)
            if writer is None:
                h, w = annotated.shape[:2]
                writer = cv2.VideoWriter(
                    str(out_dir / "annotated.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h)
                )
            writer.write(annotated)

        frame_index += 1
        if frame_index % 50 == 0 or frame_index == total:
            elapsed = time.perf_counter() - start
            eta = elapsed / frame_index * (total - frame_index)
            print(f"  {frame_index}/{total} frames  ({frame_index / elapsed:.1f} fps, ETA {eta:.0f}s)", flush=True)

    cap.release()
    if writer is not None:
        writer.release()
    wall = time.perf_counter() - start

    if not records:
        raise SystemExit("No se procesó ningún frame.")

    csv_path = out_dir / "frames.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        w.writeheader()
        w.writerows(records)

    summary = build_summary(records, str(video_path), str(model_path), fps, wall)
    with open(out_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\nGenerando gráficos...")
    make_charts(records, out_dir)

    print(f"\n{'=' * 60}\nRESUMEN")
    dr = summary["detection_rate"]
    print(f"  Frames procesados:     {summary['total_frames']} en {wall:.0f}s "
          f"({summary['inference_ms']['throughput_fps']} fps)")
    print(f"  Detección pupila:      {dr['pupil'] * 100:.1f}%  (sin detectar: {summary['frames_without_pupil']})")
    print(f"  Detección iris:        {dr['iris'] * 100:.1f}%  (sin detectar: {summary['frames_without_iris']})")
    print(f"  Ratio pupila/iris:     media {summary['pupil_iris_ratio']['mean']}  "
          f"(min {summary['pupil_iris_ratio']['min']}, max {summary['pupil_iris_ratio']['max']})")
    print(f"  Confianza pupila:      media {summary['pupil_confidence']['mean']}")
    print(f"  Confianza iris:        media {summary['iris_confidence']['mean']}")
    print(f"  Latencia inferencia:   media {summary['inference_ms']['mean']}ms  "
          f"p95 {summary['inference_ms']['p95']}ms")
    print(f"{'=' * 60}")
    print(f"\nSalidas en {out_dir}:")
    for name in ("annotated.mp4", "frames.csv", "summary.json", "charts/"):
        p = out_dir / name
        if p.exists():
            print(f"  - {name}")


if __name__ == "__main__":
    main()
