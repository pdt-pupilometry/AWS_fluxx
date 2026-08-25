"""Sube el consolidado a S3 y genera la URL prefirmada."""

from __future__ import annotations

from serialize import FrameSerializer
from typing import Protocol
import boto3
import gzip

class DeliverableStore(Protocol):
    def put_bytes(self, bucket: str, key: str, body: bytes, **put_kwargs) -> None: ...

    def presign_get(self, bucket: str, key: str, expires_in: int) -> str: ...

class S3DeliverableStore:
    def __init__(self, client=None) -> None:
        self._s3 = client or boto3.client("s3")

    def put_bytes(self, bucket: str, key: str, body: bytes, **put_kwargs) -> None:
        self._s3.put_object(Bucket=bucket, Key=key, Body=body, **put_kwargs)

    def presign_get(self, bucket: str, key: str, expires_in: int) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )

class DeliverablePublisher:
    def __init__(
        self,
        store: DeliverableStore,
        serializer: FrameSerializer,
        gzip_file: bool,
        expires_in: int,
    ) -> None:
        self._store = store
        self._serializer = serializer
        self._gzip_file = gzip_file
        self._expires_in = expires_in

    def publish(
        self,
        bucket: str,
        execution_name: str,
        public_frames: list[dict],
    ) -> tuple[str, str, str]:
        body = self._serializer.serialize(public_frames)
        put_kwargs: dict = {"ContentType": self._serializer.content_type}
        if self._gzip_file:
            body = gzip.compress(body)
            put_kwargs["ContentEncoding"] = "gzip"

        key = f"deliverables/{execution_name}/frames.{self._serializer.extension}"
        self._store.put_bytes(bucket, key, body, **put_kwargs)
        url = self._store.presign_get(bucket, key, self._expires_in)
        return key, self._serializer.content_type, url
