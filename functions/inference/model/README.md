# Modelo YOLO26 (ONNX)

Coloca aquí el modelo de segmentación ya exportado a ONNX con el nombre exacto:

```
yolo26l_seg.onnx
```

Este repo solo despliega e infiere con ONNX Runtime. El archivo `.onnx` debe
estar listo antes de correr `./scripts/deploy.sh` (no se genera ni se entrena
desde aquí).

Clases esperadas por el pipeline:
- **Clase 0:** pupila
- **Clase 1:** iris

El modelo se hornea dentro de la imagen Docker de la Lambda de inferencia
(`functions/inference/Dockerfile`), así el cold start no descarga nada de S3.
El archivo `.onnx` no se sube a git (ver `.gitignore`).
