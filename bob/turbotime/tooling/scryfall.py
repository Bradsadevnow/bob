from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, Optional

import requests

from bob.turbotime.tooling.base import ToolResult, tool_output

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"


class ScryfallLookupTool:
    def __init__(self, *, cache_dir: str, ttl_seconds: int = 60 * 60 * 24) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        os.makedirs(self.cache_dir, exist_ok=True)

    def run(self, *, args: Dict[str, Any]) -> ToolResult:
        name = (args.get("name") or args.get("id") or "").strip()
        if not name:
            err = "Missing card name."
            return ToolResult(
                tool_name="scryfall.lookup",
                args=args,
                status="error",
                output=tool_output(status="error", provider="scryfall", confidence="verbatim", error=err),
                error=err,
            )

        cached = self._get_cached(name)
        if cached is not None:
            return ToolResult(
                tool_name="scryfall.lookup",
                args={"name": name},
                status="ok",
                output=tool_output(
                    status="ok",
                    provider="scryfall",
                    confidence="verbatim",
                    cache="hit",
                    data={"card": cached},
                ),
            )

        try:
            resp = requests.get(SCRYFALL_NAMED_URL, params={"fuzzy": name}, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            err = f"Scryfall request failed: {e}"
            return ToolResult(
                tool_name="scryfall.lookup",
                args={"name": name},
                status="error",
                output=tool_output(status="error", provider="scryfall", confidence="verbatim", error=err),
                error=err,
            )

        if raw.get("object") == "error":
            err = raw.get("details", "Unknown Scryfall error.")
            return ToolResult(
                tool_name="scryfall.lookup",
                args={"name": name},
                status="error",
                output=tool_output(
                    status="error",
                    provider="scryfall",
                    confidence="verbatim",
                    data={"raw": raw},
                    error=err,
                ),
                error=err,
            )

        card = self._normalize(raw)
        self._set_cached(name, card)
        return ToolResult(
            tool_name="scryfall.lookup",
            args={"name": name},
            status="ok",
            output=tool_output(
                status="ok",
                provider="scryfall",
                confidence="verbatim",
                cache="miss",
                data={"card": card},
            ),
        )

    def _normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "name": raw.get("name"),
            "mana_cost": raw.get("mana_cost"),
            "cmc": raw.get("cmc"),
            "colors": raw.get("colors"),
            "color_identity": raw.get("color_identity"),
            "type_line": raw.get("type_line"),
            "oracle_text": raw.get("oracle_text"),
            "keywords": raw.get("keywords", []),
            "legalities": raw.get("legalities", {}),
            "set": raw.get("set"),
            "collector_number": raw.get("collector_number"),
            "scryfall_uri": raw.get("scryfall_uri"),
        }

    def _cache_key(self, name: str) -> str:
        norm = name.strip().lower().encode("utf-8")
        return hashlib.sha256(norm).hexdigest()

    def _cache_path(self, name: str) -> str:
        return os.path.join(self.cache_dir, f"{self._cache_key(name)}.json")

    def _get_cached(self, name: str) -> Optional[Dict[str, Any]]:
        path = self._cache_path(name)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except Exception:
            return None
        ts = entry.get("_ts")
        card = entry.get("card")
        if not ts or not card:
            return None
        try:
            if (time.time() - float(ts)) > self.ttl_seconds:
                return None
        except Exception:
            return None
        return card

    def _set_cached(self, name: str, card: Dict[str, Any]) -> None:
        path = self._cache_path(name)
        entry = {"_ts": time.time(), "name": name, "card": card}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
