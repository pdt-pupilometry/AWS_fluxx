"""
Inferencia YOLO26-seg (ONNX Runtime) + reconstrucción de máscara + geometría
de elipses para pupila (clase 0) e iris (clase 1).

Soporta dos formatos de salida de export, detectados por shape:
  - E2E (NMS-free, propio de YOLO26/YOLOv10): (1, 300, 4+1+1+nm)
    cada fila ya es una detección final: [x1,y1,x2,y2, conf, class_id, mask_coefs...]
  - Clásico (Ultralytics YOLOv8-seg): (1, 4+nc+nm, 8400)
    hay que transponer y NO viene resuelta la clase: [cx,cy,w,h, score_c0, score_c1, mask_coefs...]

Como solo se necesita la mejor detección de pupila y la mejor de iris por
frame (no una escena con múltiples objetos), un argmax por clase reemplaza
al NMS completo — más simple y más rápido para este caso de uso.
"""

from __future__ import annotations

import math
import os

import cv2
import numpy as np
import onnxruntime as ort

MODEL_PATH = os.environ.get("MODEL_PATH", "/var/task/model/yolo26l_seg.onnx")
CONF_THRESHOLD = float(os.environ.get("CONF_THRESHOLD", "0.25"))
INPUT_SIZE = 640
MASK_THRESHOLD = 0.5

CLASS_PUPIL = 0
CLASS_IRIS = 1

# La sesión ONNX se crea UNA vez por contenedor, en el primer uso (lazy):
# las invocaciones que reutilizan un contenedor tibio no vuelven a cargar el
# modelo. La carga perezosa también permite importar este módulo sin el .onnx.
_session = None
_input_name = None


def _ort_threads() -> int:
    # En Lambda, cpuinfo no puede leer /sys/devices/system/cpu (Firecracker),
    # asi que ORT no autodetecta los cores. Se derivan de la memoria asignada:
    # Lambda entrega ~1 vCPU por cada 1769 MB. Fuera de Lambda (p. ej. Docker
    # local) se usa os.cpu_count().
    lambda_mb = os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE")
    if lambda_mb:
        return max(1, round(int(lambda_mb) / 1769))
    return os.cpu_count() or 2


def _get_session() -> tuple[ort.InferenceSession, str]:
    global _session, _input_name
    if _session is None:
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = _ort_threads()
        _session = ort.InferenceSession(MODEL_PATH, sess_options=options, providers=["CPUExecutionProvider"])
        _input_name = _session.get_inputs()[0].name
    return _session, _input_name


def letterbox(img_bgr: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, int, int]:
    """Redimensiona conservando el aspecto y rellena con gris (estilo Ultralytics)."""
    h, w = img_bgr.shape[:2]
    ratio = min(size / h, size / w)
    new_h, new_w = round(h * ratio), round(w * ratio)
    resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    pad_top = (size - new_h) // 2
    pad_left = (size - new_w) // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[pad_top : pad_top + new_h, pad_left : pad_left + new_w] = resized
    return canvas, ratio, pad_left, pad_top


def _best_from_classic(class_id: int, boxes_xyxy, scores, mask_coefs):
    class_scores = scores[:, class_id]
    best_idx = int(np.argmax(class_scores))
    confidence = float(class_scores[best_idx])
    if confidence < CONF_THRESHOLD:
        return None
    return boxes_xyxy[best_idx], mask_coefs[best_idx], confidence


def _best_from_e2e(class_id: int, boxes_xyxy, conf, cls_id, mask_coefs):
    candidates = np.where(cls_id == class_id)[0]
    if candidates.size == 0:
        return None
    best_local = candidates[np.argmax(conf[candidates])]
    confidence = float(conf[best_local])
    if confidence < CONF_THRESHOLD:
        return None
    return boxes_xyxy[best_local], mask_coefs[best_local], confidence


def best_detections_per_class(outputs: list[np.ndarray]) -> tuple[dict | None, dict | None, np.ndarray]:
    """
    Devuelve (deteccion_pupila, deteccion_iris, protos), donde cada deteccion
    es {"box_xyxy": np.ndarray[4], "mask_coef": np.ndarray[nm], "confidence": float}
    o None si no se detecta por encima de CONF_THRESHOLD.
    """
    preds = np.squeeze(outputs[0], axis=0)
    protos = np.squeeze(outputs[1], axis=0)  # (nm, mh, mw)
    nm = protos.shape[0]

    if preds.shape[0] > preds.shape[1]:
        # Formato E2E: (num_detecciones=300, 4+1+1+nm)
        boxes_xyxy = preds[:, 0:4]
        conf = preds[:, 4]
        cls_id = preds[:, 5].astype(int)
        mask_coefs = preds[:, 6 : 6 + nm]
        pupil = _best_from_e2e(CLASS_PUPIL, boxes_xyxy, conf, cls_id, mask_coefs)
        iris = _best_from_e2e(CLASS_IRIS, boxes_xyxy, conf, cls_id, mask_coefs)
    else:
        # Formato clasico: (4+nc+nm, num_anchors=8400) -> transponer
        preds = preds.T
        nc = preds.shape[1] - 4 - nm
        cx, cy, w, h = preds[:, 0], preds[:, 1], preds[:, 2], preds[:, 3]
        boxes_xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)
        scores = preds[:, 4 : 4 + nc]
        mask_coefs = preds[:, 4 + nc : 4 + nc + nm]
        pupil = _best_from_classic(CLASS_PUPIL, boxes_xyxy, scores, mask_coefs)
        iris = _best_from_classic(CLASS_IRIS, boxes_xyxy, scores, mask_coefs)

    pupil_det = {"box_xyxy": pupil[0], "mask_coef": pupil[1], "confidence": pupil[2]} if pupil else None
    iris_det = {"box_xyxy": iris[0], "mask_coef": iris[1], "confidence": iris[2]} if iris else None
    return pupil_det, iris_det, protos


def build_mask(
    detection: dict | None,
    protos: np.ndarray,
    ratio: float,
    pad_left: int,
    pad_top: int,
    orig_shape: tuple[int, int],
) -> np.ndarray | None:
    """Reconstruye la máscara binaria de una detección a la resolución original del frame."""
    if detection is None:
        return None

    nm, mask_h, mask_w = protos.shape
    mask = detection["mask_coef"] @ protos.reshape(nm, -1)
    mask = 1.0 / (1.0 + np.exp(-mask))  # sigmoide
    mask = mask.reshape(mask_h, mask_w)

    # Recortar al bounding box (evita activaciones espurias fuera del objeto)
    x1, y1, x2, y2 = detection["box_xyxy"]
    sx, sy = mask_w / INPUT_SIZE, mask_h / INPUT_SIZE
    mx1 = max(int(x1 * sx), 0)
    my1 = max(int(y1 * sy), 0)
    mx2 = min(int(math.ceil(x2 * sx)), mask_w)
    my2 = min(int(math.ceil(y2 * sy)), mask_h)
    cropped = np.zeros_like(mask)
    cropped[my1:my2, mx1:mx2] = mask[my1:my2, mx1:mx2]

    # Escalar a la entrada del modelo, quitar el padding del letterbox y
    # volver a la resolución original del frame (480x640)
    mask_full = cv2.resize(cropped, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    orig_h, orig_w = orig_shape
    content_h, content_w = round(orig_h * ratio), round(orig_w * ratio)
    mask_content = mask_full[pad_top : pad_top + content_h, pad_left : pad_left + content_w]
    mask_orig = cv2.resize(mask_content, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)

    return (mask_orig > MASK_THRESHOLD).astype(np.uint8)


def ellipse_area_from_mask(mask: np.ndarray | None) -> float:
    """
    Extrae el contorno mayor de la máscara y ajusta la elipse de mejor calce.
    cv2.fitEllipseDirect exige >= 5 puntos: durante parpadeos u oclusiones el
    contorno puede ser degenerado, por lo que cualquier fallo devuelve 0.
    """
    if mask is None:
        return 0.0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return 0.0
    contour = max(contours, key=cv2.contourArea)
    try:
        if len(contour) < 5:
            raise ValueError("fitEllipseDirect requiere al menos 5 puntos")
        (_, _), (major_axis, minor_axis), _ = cv2.fitEllipseDirect(contour)
        # fitEllipseDirect devuelve los ejes completos: area = pi * (MA/2) * (ma/2)
        return float(math.pi * major_axis * minor_axis / 4.0)
    except (cv2.error, ValueError):
        return 0.0  # oclusión / parpadeo


def compute_pupil_iris_ratio(pupil_area: float, iris_area: float) -> float:
    """pupil_area/iris_area solo si iris_area > 0; evita ZeroDivisionError
    cuando el iris no se detecta (oclusión/parpadeo)."""
    return round(pupil_area / iris_area, 6) if iris_area > 0 else 0.0


def infer_frame_metrics(img_gray: np.ndarray) -> dict:
    """Corre el modelo sobre un frame en escala de grises y devuelve las 5 métricas."""
    img_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
    canvas, ratio, pad_left, pad_top = letterbox(img_bgr)
    blob = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]

    session, input_name = _get_session()
    outputs = session.run(None, {input_name: blob})
    pupil_det, iris_det, protos = best_detections_per_class(outputs)

    orig_shape = img_gray.shape[:2]
    pupil_mask = build_mask(pupil_det, protos, ratio, pad_left, pad_top, orig_shape)
    iris_mask = build_mask(iris_det, protos, ratio, pad_left, pad_top, orig_shape)

    pupil_area = ellipse_area_from_mask(pupil_mask)
    iris_area = ellipse_area_from_mask(iris_mask)

    return {
        "pupil_area_pixels": round(pupil_area, 2),
        "iris_area_pixels": round(iris_area, 2),
        "pupil_iris_ratio": compute_pupil_iris_ratio(pupil_area, iris_area),
        "pupil_confidence": round(pupil_det["confidence"], 4) if pupil_det else 0.0,
        "iris_confidence": round(iris_det["confidence"], 4) if iris_det else 0.0,
    }
