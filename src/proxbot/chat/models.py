"""LLM backend model discovery and selection."""

import json
import pathlib
import sys
from typing import List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

def _server_root(base_url: str) -> str:
    """Strip a trailing /v1 so backend-native endpoints can be probed."""
    root = base_url.rstrip("/")
    if root.lower().endswith("/v1"):
        root = root[: -len("/v1")].rstrip("/") or base_url.rstrip("/")
    return root


def fetch_backend_models(base_url: str, api_key: str, timeout: int = 10) -> List[str]:
    """Return backend model IDs, loaded models first.

    Probes LM Studio's management API (GET /api/v0/models, which reports
    per-model load state) and prefers loaded LLM/VLM entries. Falls back to
    the OpenAI-compatible model list (GET /models), which on some backends is
    the full catalog rather than what's loaded. Returns [] when unreachable.
    """
    if not requests:
        return []
    headers = {"Authorization": f"Bearer {api_key or 'not-needed'}"}
    try:
        resp = requests.get(f"{_server_root(base_url)}/api/v0/models", headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            loaded = [
                m.get("id", "") for m in data
                if isinstance(m, dict) and m.get("state") == "loaded"
                and m.get("type", "llm") in ("llm", "vlm") and m.get("id")
            ]
            if loaded:
                return loaded
    except Exception:
        pass
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/models",
            headers=headers,
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []
        data = resp.json().get("data", [])
        return [m.get("id", "") for m in data if isinstance(m, dict) and m.get("id")]
    except Exception:
        return []


def resolve_model(
    base_url: str,
    api_key: str,
    requested: Optional[str],
    fallback: str,
) -> Tuple[str, Optional[str]]:
    """Pick the chat model: explicit request wins, otherwise use the backend's loaded model.

    Returns (model_id, notice) where notice explains a fallback/detection outcome.
    """
    if requested and requested.strip().lower() != "auto":
        return requested.strip(), None
    detected = fetch_backend_models(base_url, api_key)
    if detected:
        return detected[0], None
    return fallback, (
        f"Backend model detection failed ({base_url}/models unreachable or empty); "
        f"using configured fallback '{fallback}'."
    )
