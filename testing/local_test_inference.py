"""
Prueba local de la Lambda de inferencia contra una imagen real, sin necesidad
de desplegar nada en AWS. Útil para validar el .onnx exportado y el umbral de
confianza antes de subir la imagen Docker.

Uso:
    python testing/local_test_inference.py --image frame_de_prueba.jpg \
        --model functions/inference/model/yolo26_seg.onnx
"""

import argparse
import os
import sys
from pathlib import Path

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Frame de prueba (jpg/png)")
    parser.add_argument(
        "--model",
        default="functions/inference/model/yolo26_seg.onnx",
        help="Ruta al modelo ONNX",
    )
    parser.add_argument("--conf-threshold", default="0.25")
    args = parser.parse_args()

    os.environ["MODEL_PATH"] = str(Path(args.model).resolve())
    os.environ["CONF_THRESHOLD"] = args.conf_threshold

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions" / "inference"))
    from yolo_onnx import infer_frame_metrics  # noqa: E402  (import tardío: depende de MODEL_PATH)

    img_gray = cv2.imread(args.image, cv2.IMREAD_GRAYSCALE)
    if img_gray is None:
        raise SystemExit(f"No se pudo leer la imagen: {args.image}")

    metrics = infer_frame_metrics(img_gray)
    print("Métricas del frame:")
    for key, value in metrics.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
