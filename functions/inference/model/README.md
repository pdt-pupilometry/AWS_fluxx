# Modelo YOLO26

Coloca aquí tu modelo de segmentación exportado a ONNX con el nombre exacto:

```
yolo26_seg.onnx
```

Usa `scripts/export_model.py` (en la raíz del repo) para exportar tu `.pt`
entrenado:

```bash
python scripts/export_model.py --weights /ruta/a/tu-modelo.pt --out functions/inference/model/yolo26_seg.onnx
```

Clases esperadas por el pipeline:
- **Clase 0:** pupila
- **Clase 1:** iris

El modelo se hornea dentro de la imagen Docker de la Lambda de inferencia
(`functions/inference/Dockerfile`), así el cold start no descarga nada de S3.
El archivo `.onnx` no se sube a git (ver `.gitignore`).
