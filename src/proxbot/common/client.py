"""Minimal Proxmox REST API client."""

import base64
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config
from .constants import REDACTED
from .logging_ import Logger
from .redact import redact_string


class ProxmoxClient:
    """Small reusable Proxmox REST API client."""

    def __init__(self, config: Config, logger: Logger, verbose: bool = False):
        self.config = config
        self.logger = logger
        self.verbose = verbose
        self.ssl_context = ssl.create_default_context()
        if not config.verify_ssl:
            self.ssl_context.check_hostname = False
            self.ssl_context.verify_mode = ssl.CERT_NONE

    def _request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.config.api_url + endpoint
        headers = {
            "Authorization": self.config.auth_header(),
            "Accept": "application/json",
        }
        body: bytes | None = None
        if data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        start = time.time()
        try:
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=self.config.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.getcode()
                duration_ms = int((time.time() - start) * 1000)
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    payload = {"_raw": raw}
                return {
                    "ok": True,
                    "status_code": status,
                    "method": method,
                    "endpoint": endpoint,
                    "data": payload.get("data", payload),
                    "error": None,
                    "duration_ms": duration_ms,
                }
        except urllib.error.HTTPError as exc:
            duration_ms = int((time.time() - start) * 1000)
            raw = exc.read().decode("utf-8", errors="replace") if exc.readable() else ""
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"_raw": raw}
            msg = payload.get("errors", raw) or str(exc)
            return {
                "ok": False,
                "status_code": exc.code,
                "method": method,
                "endpoint": endpoint,
                "data": payload.get("data", payload),
                "error": msg,
                "duration_ms": duration_ms,
            }
        except urllib.error.URLError as exc:
            duration_ms = int((time.time() - start) * 1000)
            reason = str(exc.reason)
            return {
                "ok": False,
                "status_code": None,
                "method": method,
                "endpoint": endpoint,
                "data": None,
                "error": reason,
                "duration_ms": duration_ms,
            }
        except TimeoutError:
            duration_ms = int((time.time() - start) * 1000)
            return {
                "ok": False,
                "status_code": None,
                "method": method,
                "endpoint": endpoint,
                "data": None,
                "error": "Request timed out",
                "duration_ms": duration_ms,
            }

    def get(self, endpoint: str) -> dict[str, Any]:
        return self._request("GET", endpoint)

    def post(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("POST", endpoint, data)

    def put(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("PUT", endpoint, data)

    def delete(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("DELETE", endpoint, data)
