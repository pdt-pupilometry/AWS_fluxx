"""
Valida que la Lambda 3 (notifier):
  1. Reconcilie los frames de ejecuciones FAILED del Distributed Map (nunca
     pierde un frame silenciosamente).
  2. Suba el consolidado como UN SOLO archivo JSON/CSV a S3 (no en el body
     del POST) y notifique al endpoint solo metadata + una URL de descarga.

No necesita AWS real: se mockea boto3 (get_object/put_object/generate_presigned_url)
y requests (post_notification).

Ejecutar:
    python -m pytest testing/test_aggregator_reconciliation.py -v
"""

import gzip
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["ENDPOINT_URL"] = "https://example.invalid/resultados"
os.environ["GZIP_FILE"] = "false"
os.environ["OUTPUT_FORMAT"] = "json"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "functions" / "notifier"))

import app  # noqa: E402


def _s3_body(payload) -> MagicMock:
    body = MagicMock()
    body.read.return_value = json.dumps(payload).encode("utf-8")
    return {"Body": body}


def _fake_get_object(bucket_contents: dict):
    def _get_object(Bucket, Key):  # noqa: N803  (nombres exigidos por la API de boto3)
        return _s3_body(bucket_contents[(Bucket, Key)])

    return _get_object


def test_collect_and_reconcile_fills_gaps_from_failed_entries():
    manifest = {
        "ResultFiles": {
            "SUCCEEDED": [{"Key": "results/exec1/SUCCEEDED_0.json"}],
            "FAILED": [{"Key": "results/exec1/FAILED_0.json"}],
        }
    }
    succeeded_entries = [
        {
            "Output": json.dumps(
                [
                    {
                        "session_id": "sesion123",
                        "eye": "left",
                        "frame_index": 0,
                        "timestamp": 0.0,
                        "pupil_area_pixels": 100.0,
                        "iris_area_pixels": 400.0,
                        "pupil_iris_ratio": 0.25,
                        "pupil_confidence": 0.9,
                        "iris_confidence": 0.95,
                    },
                    {
                        "session_id": "sesion123",
                        "eye": "left",
                        "frame_index": 1,
                        "timestamp": 0.033,
                        "pupil_area_pixels": 110.0,
                        "iris_area_pixels": 410.0,
                        "pupil_iris_ratio": 0.268,
                        "pupil_confidence": 0.88,
                        "iris_confidence": 0.94,
                    },
                ]
            )
        }
    ]
    # Frame índice 2 quedó en una ejecución hija FAILED (p.ej. timeout de infraestructura)
    failed_entries = [
        {
            "Input": json.dumps(
                {
                    "BatchInput": {
                        "frames_bucket": "frames-bucket",
                        "session_id": "sesion123",
                        "eye": "left",
                    },
                    "Items": [{"frame_key": "frames/exec1/f000002_t66.jpg"}],
                }
            ),
            "Error": "States.Timeout",
        }
    ]

    bucket_contents = {
        ("frames-bucket", "manifest.json"): manifest,
        ("frames-bucket", "results/exec1/SUCCEEDED_0.json"): succeeded_entries,
        ("frames-bucket", "results/exec1/FAILED_0.json"): failed_entries,
    }

    with patch.object(app.s3, "get_object", side_effect=_fake_get_object(bucket_contents)):
        frames = app.collect_and_reconcile({"Bucket": "frames-bucket", "Key": "manifest.json"})

    frames.sort(key=lambda f: f["frame_index"])

    assert len(frames) == 3
    assert [f["frame_index"] for f in frames] == [0, 1, 2]

    reconciled = frames[2]
    assert reconciled["session_id"] == "sesion123"
    assert reconciled["eye"] == "left"
    assert reconciled["timestamp"] == 0.066
    assert reconciled["pupil_area_pixels"] == 0.0
    assert reconciled["iris_area_pixels"] == 0.0
    assert reconciled["pupil_iris_ratio"] == 0.0
    assert reconciled["pupil_confidence"] == 0.0
    assert reconciled["iris_confidence"] == 0.0


def _build_sample_manifest_and_bucket(exec_name: str, session_id: str, eye: str):
    manifest = {
        "ResultFiles": {
            "SUCCEEDED": [{"Key": f"results/{exec_name}/SUCCEEDED_0.json"}],
            "FAILED": [{"Key": f"results/{exec_name}/FAILED_0.json"}],
        }
    }
    succeeded_entries = [
        {
            "Output": json.dumps(
                [
                    {
                        "session_id": session_id,
                        "eye": eye,
                        "frame_index": 0,
                        "timestamp": 0.0,
                        "pupil_area_pixels": 50.0,
                        "iris_area_pixels": 200.0,
                        "pupil_iris_ratio": 0.25,
                        "pupil_confidence": 0.8,
                        "iris_confidence": 0.9,
                    }
                ]
            )
        }
    ]
    # Frame 1 quedó en una ejecución hija FAILED (fallo de infraestructura)
    failed_entries = [
        {
            "Input": json.dumps(
                {
                    "BatchInput": {"frames_bucket": "frames-bucket", "session_id": session_id, "eye": eye},
                    "Items": [{"frame_key": f"frames/{exec_name}/f000001_t33.jpg"}],
                }
            )
        }
    ]
    bucket_contents = {
        ("frames-bucket", "manifest.json"): manifest,
        ("frames-bucket", f"results/{exec_name}/SUCCEEDED_0.json"): succeeded_entries,
        ("frames-bucket", f"results/{exec_name}/FAILED_0.json"): failed_entries,
    }
    return bucket_contents


def test_lambda_handler_uploads_single_file_and_notifies_with_link():
    bucket_contents = _build_sample_manifest_and_bucket("exec2", "sesion999", "right")

    event = {
        "job": {
            "session_id": "sesion999",
            "eye": "right",
            "fps": 30.0,
            "total_frames": 2,
            "source_video": "s3://videos-bucket/sesion999_right.mp4",
            "frames_bucket": "frames-bucket",
            "execution_name": "exec2",
        },
        "result_writer": {"Bucket": "frames-bucket", "Key": "manifest.json"},
    }

    put_object_calls = []
    notifications = []

    def _fake_put_object(**kwargs):
        put_object_calls.append(kwargs)

    def _fake_post_notification(payload):
        notifications.append(payload)

    with patch.object(app.s3, "get_object", side_effect=_fake_get_object(bucket_contents)), patch.object(
        app.s3, "put_object", side_effect=_fake_put_object
    ), patch.object(
        app.s3, "generate_presigned_url", return_value="https://example.invalid/presigned-url"
    ), patch.object(
        app, "post_notification", side_effect=_fake_post_notification
    ):
        result = app.lambda_handler(event, None)

    # Un solo archivo subido, no 1000 escrituras ni un body gigante
    assert result["frames_processed"] == 2
    assert len(put_object_calls) == 1
    assert len(notifications) == 1

    notif = notifications[0]
    assert notif["total_frames"] == 2
    assert notif["download_url"] == "https://example.invalid/presigned-url"
    assert notif["format"] == "json"
    assert "frames" not in notif  # la data NO viaja en el body de la notificacion

    uploaded_frames = json.loads(put_object_calls[0]["Body"])
    assert len(uploaded_frames) == 2
    # el formato publico no debe filtrar frame_index (uso interno de ordenamiento)
    assert "frame_index" not in uploaded_frames[0]


def test_upload_deliverable_gzips_when_enabled():
    with patch.object(app, "GZIP_FILE", True), patch.object(app, "OUTPUT_FORMAT", "json"):
        put_object_calls = []
        with patch.object(app.s3, "put_object", side_effect=lambda **kw: put_object_calls.append(kw)):
            key, content_type = app.upload_deliverable("frames-bucket", "exec3", [{"session_id": "s1"}])

    assert key == "deliverables/exec3/frames.json"
    assert content_type == "application/json"
    assert put_object_calls[0]["ContentEncoding"] == "gzip"
    decompressed = gzip.decompress(put_object_calls[0]["Body"])
    assert json.loads(decompressed) == [{"session_id": "s1"}]


def test_upload_deliverable_csv_format():
    sample_frames = [
        {
            "session_id": "sesion1",
            "eye": "left",
            "timestamp": 0.033,
            "pupil_area_pixels": 10.5,
            "iris_area_pixels": 40.0,
            "pupil_iris_ratio": 0.2625,
            "pupil_confidence": 0.9,
            "iris_confidence": 0.95,
        }
    ]
    with patch.object(app, "GZIP_FILE", False), patch.object(app, "OUTPUT_FORMAT", "csv"):
        put_object_calls = []
        with patch.object(app.s3, "put_object", side_effect=lambda **kw: put_object_calls.append(kw)):
            key, content_type = app.upload_deliverable("frames-bucket", "exec4", sample_frames)

    assert key == "deliverables/exec4/frames.csv"
    assert content_type == "text/csv"
    csv_text = put_object_calls[0]["Body"].decode("utf-8")
    assert "session_id,eye,timestamp" in csv_text.splitlines()[0]
    assert "sesion1,left,0.033" in csv_text


if __name__ == "__main__":
    test_collect_and_reconcile_fills_gaps_from_failed_entries()
    test_lambda_handler_uploads_single_file_and_notifies_with_link()
    test_upload_deliverable_gzips_when_enabled()
    test_upload_deliverable_csv_format()
    print("OK: test_aggregator_reconciliation.py")
