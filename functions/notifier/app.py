"""Lambda 3 entrypoint: reconcile → serialize → publish → notify."""

from __future__ import annotations

from deliverable import DeliverablePublisher, S3DeliverableStore
from serialize import serializer_for, to_public_frames
from datetime import datetime, timedelta, timezone
from reconcile import ResultReconciler
from notify import EndpointNotifier
from config import NotifierConfig
from s3_store import S3JsonStore

_config = NotifierConfig.from_env()
_reconciler = ResultReconciler(S3JsonStore())
_publisher = DeliverablePublisher(
    store=S3DeliverableStore(),
    serializer=serializer_for(_config.output_format),
    gzip_file=_config.gzip_file,
    expires_in=_config.presigned_url_expiration_seconds,
)
_notifier = EndpointNotifier(_config)

def lambda_handler(event, context):
    job = event["job"]
    frames, frames_failed = _reconciler.collect(event["result_writer"])
    frames.sort(key=lambda f: f["frame_index"])

    total_expected = job["total_frames"]
    if len(frames) != total_expected:
        print(
            f"ADVERTENCIA: se esperaban {total_expected} frames y se armaron {len(frames)} "
            f"para session_id={job['session_id']} eye={job['eye']}"
        )

    public_frames = to_public_frames(frames)
    deliverable_key, content_type, download_url = _publisher.publish(
        job["frames_bucket"],
        job["execution_name"],
        public_frames,
    )
    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(seconds=_config.presigned_url_expiration_seconds)
    ).isoformat()

    _notifier.post(
        {
            "session_id": job["session_id"],
            "eye": job["eye"],
            "video_key": job["source_video"],
            "fps": job["fps"],
            "total_frames": total_expected,
            "frames_failed": frames_failed,
            "format": _config.output_format,
            "content_type": content_type,
            "compressed": _config.gzip_file,
            "download_url": download_url,
            "expires_at": expires_at,
        }
    )

    print(
        f"Notificado {_config.endpoint_url}: {len(public_frames)} frames en formato "
        f"{_config.output_format} disponibles en "
        f"s3://{job['frames_bucket']}/{deliverable_key}"
    )
    return {
        "session_id": job["session_id"],
        "eye": job["eye"],
        "frames_processed": len(public_frames),
        "frames_failed": frames_failed,
        "deliverable_s3_key": deliverable_key,
        "endpoint": _config.endpoint_url,
    }
