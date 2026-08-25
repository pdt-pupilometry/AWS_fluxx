from __future__ import annotations
import numpy as np
import math
import cv2
import os
from onnx_session import get_session

CONF_THRESHOLD = float(os.environ.get("CONF_THRESHOLD", "0.25"))
INPUT_SIZE = 640
MASK_THRESHOLD = 0.5
CLASS_PUPIL = 0
CLASS_IRIS = 1

def letterbox(img_bgr: np.ndarray, size: int = INPUT_SIZE) -> tuple[np.ndarray, float, int, int]:
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
    preds = np.squeeze(outputs[0], axis=0)
    protos = np.squeeze(outputs[1], axis=0)
    nm = protos.shape[0]

    if preds.shape[0] > preds.shape[1]:
        boxes_xyxy = preds[:, 0:4]
        conf = preds[:, 4]
        cls_id = preds[:, 5].astype(int)
        mask_coefs = preds[:, 6 : 6 + nm]
        pupil = _best_from_e2e(CLASS_PUPIL, boxes_xyxy, conf, cls_id, mask_coefs)
        iris = _best_from_e2e(CLASS_IRIS, boxes_xyxy, conf, cls_id, mask_coefs)
    else:
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
    if detection is None:
        return None

    nm, mask_h, mask_w = protos.shape
    mask = detection["mask_coef"] @ protos.reshape(nm, -1)
    mask = 1.0 / (1.0 + np.exp(-mask))
    mask = mask.reshape(mask_h, mask_w)

    x1, y1, x2, y2 = detection["box_xyxy"]
    sx, sy = mask_w / INPUT_SIZE, mask_h / INPUT_SIZE
    mx1 = max(int(x1 * sx), 0)
    my1 = max(int(y1 * sy), 0)
    mx2 = min(int(math.ceil(x2 * sx)), mask_w)
    my2 = min(int(math.ceil(y2 * sy)), mask_h)
    cropped = np.zeros_like(mask)
    cropped[my1:my2, mx1:mx2] = mask[my1:my2, mx1:mx2]

    mask_full = cv2.resize(cropped, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
    orig_h, orig_w = orig_shape
    content_h, content_w = round(orig_h * ratio), round(orig_w * ratio)
    mask_content = mask_full[pad_top : pad_top + content_h, pad_left : pad_left + content_w]
    mask_orig = cv2.resize(mask_content, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
    return (mask_orig > MASK_THRESHOLD).astype(np.uint8)

def ellipse_area_from_mask(mask: np.ndarray | None) -> float:
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
        return float(math.pi * major_axis * minor_axis / 4.0)
    except (cv2.error, ValueError):
        return 0.0

def compute_pupil_iris_ratio(pupil_area: float, iris_area: float) -> float:
    return round(pupil_area / iris_area, 6) if iris_area > 0 else 0.0

def infer_frame_metrics(img_gray: np.ndarray) -> dict:
    img_bgr = cv2.cvtColor(img_gray, cv2.COLOR_GRAY2BGR)
    canvas, ratio, pad_left, pad_top = letterbox(img_bgr)
    blob = (canvas.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]

    session, input_name = get_session()
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
