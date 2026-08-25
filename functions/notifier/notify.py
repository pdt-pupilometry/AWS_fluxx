"""POST al endpoint externo con reintentos y backoff."""

from __future__ import annotations

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import NotifierConfig
from typing import Protocol
import requests

class HttpNotifier(Protocol):
    def post(self, payload: dict) -> None: ...

class EndpointNotifier:
    def __init__(self, config: NotifierConfig, session: requests.Session | None = None) -> None:
        self._url = config.endpoint_url
        self._api_key = config.endpoint_api_key
        self._session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        retry = Retry(
            total=5,
            backoff_factor=1.0,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            respect_retry_after_header=True,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    def post(self, payload: dict) -> None:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        response = self._session.post(self._url, json=payload, headers=headers, timeout=(5, 30))
        response.raise_for_status()
