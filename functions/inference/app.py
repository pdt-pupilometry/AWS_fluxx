"""Lambda 2 entrypoint: wiring del processor. Contrato del Distributed Map intacto."""

from __future__ import annotations

from processor import FrameProcessor, S3ObjectStore
from yolo_onnx import infer_frame_metrics

_processor = FrameProcessor(store=S3ObjectStore(), infer=infer_frame_metrics)

def lambda_handler(event, context):
    batch_input = event["BatchInput"]
    bucket = batch_input["frames_bucket"]
    session_id = batch_input["session_id"]
    eye = batch_input["eye"]

    results = []
    for item in event["Items"]:
        frame_key = item["frame_key"]
        if not frame_key.endswith(".jpg"):
            continue
        results.append(_processor.process(bucket, frame_key, session_id, eye))
    return results
