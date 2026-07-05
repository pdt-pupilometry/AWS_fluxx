"""
Exporta un modelo YOLO26-seg entrenado (.pt) a ONNX para la Lambda de inferencia.

Uso:
    python scripts/export_model.py --weights /ruta/a/tu-modelo.pt \
        --out functions/inference/model/yolo26_seg.onnx

Requiere `ultralytics` y `onnx` instalados localmente (no hace falta en Lambda,
solo aquí para exportar una vez).
"""

import argparse
import shutil
from pathlib import Path

import onnxruntime as ort
from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True, help="Ruta al .pt entrenado")
    parser.add_argument(
        "--out",
        default="functions/inference/model/yolo26_seg.onnx",
        help="Ruta de salida del .onnx",
    )
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--opset",
        type=int,
        default=17,
        help="Opset ONNX. 17 es el valor seguro probado con onnxruntime==1.19.2 "
        "(el default de Ultralytics puede exportar a un opset mas nuevo que "
        "torch.onnx todavia no soporta del todo, p.ej. 22).",
    )
    args = parser.parse_args()

    model = YOLO(args.weights)
    exported_path = model.export(format="onnx", imgsz=args.imgsz, opset=args.opset, simplify=True)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(exported_path), str(out_path))

    print(f"Modelo exportado a {out_path}")

    session = ort.InferenceSession(str(out_path), providers=["CPUExecutionProvider"])
    print("Inputs:")
    for inp in session.get_inputs():
        print(f"  {inp.name}: {inp.shape}")
    print("Outputs:")
    for out in session.get_outputs():
        print(f"  {out.name}: {out.shape}")
    print(
        "\nVerifica que el primer output tenga shape (1, 300, 4+1+1+nm) [E2E] "
        "o (1, 4+nc+nm, 8400) [clasico] — ambos formatos soportados por yolo_onnx.py."
    )


if __name__ == "__main__":
    main()
