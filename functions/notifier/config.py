"""Configuración de la Lambda notifier (desde env)."""

from __future__ import annotations

from dataclasses import dataclass
import os

@dataclass(frozen=True)
class NotifierConfig:
    endpoint_url: str
    endpoint_api_key: str = ""
    output_format: str = "json"
    gzip_file: bool = True
    presigned_url_expiration_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "NotifierConfig":
        return cls(
            endpoint_url=os.environ["ENDPOINT_URL"],
            endpoint_api_key=os.environ.get("ENDPOINT_API_KEY", ""),
            output_format=os.environ.get("OUTPUT_FORMAT", "json").lower(),
            gzip_file=os.environ.get("GZIP_FILE", "true").lower() == "true",
            presigned_url_expiration_seconds=int(
                os.environ.get("PRESIGNED_URL_EXPIRATION_SECONDS", "3600")
            ),
        )
