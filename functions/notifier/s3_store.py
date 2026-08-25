"""Lectura JSON desde S3 (manifest / SUCCEEDED / FAILED)."""

from __future__ import annotations

import boto3
import json

class S3JsonStore:
    def __init__(self, client=None) -> None:
        self._s3 = client or boto3.client("s3")

    def read_json(self, bucket: str, key: str):
        body = self._s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
