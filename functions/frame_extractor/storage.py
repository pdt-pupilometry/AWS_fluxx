"""Almacenamiento S3 de frames + uploader paralelo (DIP via Protocol)."""

from __future__ import annotations

from naming import frame_object_key
from preprocess import EncodedFrame
from typing import Protocol
import concurrent.futures
import boto3

class FrameStore(Protocol):
    def download_video(self, bucket: str, key: str, local_path: str) -> None: ...

    def upload_encoded_frame(
        self,
        frames_prefix: str,
        encoded: EncodedFrame,
        session_id: str,
        eye: str,
    ) -> None: ...

class S3FrameStore:
    def __init__(self, frames_bucket: str, client=None) -> None:
        self._frames_bucket = frames_bucket
        self._s3 = client or boto3.client("s3")

    def download_video(self, bucket: str, key: str, local_path: str) -> None:
        self._s3.download_file(bucket, key, local_path)

    def upload_encoded_frame(
        self,
        frames_prefix: str,
        encoded: EncodedFrame,
        session_id: str,
        eye: str,
    ) -> None:
        key = frame_object_key(frames_prefix, encoded.index, encoded.timestamp_ms)
        self._s3.put_object(
            Bucket=self._frames_bucket,
            Key=key,
            Body=encoded.jpeg_bytes,
            ContentType="image/jpeg",
            Metadata={
                "session-id": session_id,
                "eye": eye,
                "frame-index": str(encoded.index),
                "timestamp-ms": str(encoded.timestamp_ms),
            },
        )

class ParallelUploader:

    def __init__(self, store: FrameStore, max_workers: int) -> None:
        self._store = store
        self._max_workers = max_workers
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._futures: list[concurrent.futures.Future] = []

    def __enter__(self) -> "ParallelUploader":
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=self._max_workers)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        assert self._pool is not None
        try:
            if exc_type is None:
                for future in concurrent.futures.as_completed(self._futures):
                    future.result()
        finally:
            self._pool.shutdown(wait=True, cancel_futures=exc_type is not None)
            self._pool = None
            self._futures.clear()

    def submit(
        self,
        frames_prefix: str,
        encoded: EncodedFrame,
        session_id: str,
        eye: str,
    ) -> None:
        assert self._pool is not None
        self._futures.append(
            self._pool.submit(
                self._store.upload_encoded_frame,
                frames_prefix,
                encoded,
                session_id,
                eye,
            )
        )
