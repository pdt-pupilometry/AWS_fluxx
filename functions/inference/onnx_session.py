"""Sesión ONNX Runtime perezosa (una por contenedor tibio)."""

from __future__ import annotations

import onnxruntime as ort
import os

MODEL_PATH = os.environ.get("MODEL_PATH", "/var/task/model/yolo26l_seg.onnx")

_session = None
_input_name = None

def ort_threads() -> int:
    lambda_mb = os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE")
    if lambda_mb:
        return max(1, round(int(lambda_mb) / 1769))
    return os.cpu_count() or 2

def get_session() -> tuple[ort.InferenceSession, str]:
    global _session, _input_name
    if _session is None:
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = ort_threads()
        _session = ort.InferenceSession(
            MODEL_PATH,
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        _input_name = _session.get_inputs()[0].name
    return _session, _input_name
