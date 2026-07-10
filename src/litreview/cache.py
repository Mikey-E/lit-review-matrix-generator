from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class ResponseCache:
    """Disk cache of raw SerpAPI JSON responses to avoid re-billing identical pages."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(params: dict[str, Any]) -> str:
        # Exclude api_key from cache identity.
        material = {k: v for k, v in sorted(params.items()) if k != "api_key"}
        blob = json.dumps(material, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def path_for(self, params: dict[str, Any]) -> Path:
        return self.root / f"{self._key(params)}.json"

    def get(self, params: dict[str, Any]) -> dict[str, Any] | None:
        path = self.path_for(params)
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None

    def put(self, params: dict[str, Any], payload: dict[str, Any]) -> Path:
        path = self.path_for(params)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return path
