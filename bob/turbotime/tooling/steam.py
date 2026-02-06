from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

import requests

from bob.turbotime.tooling.base import ToolResult, tool_output

STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"


class SteamGameLookupTool:
    def __init__(self, *, cache_dir: str, ttl_seconds: int = 60 * 60 * 24) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.search_dir = os.path.join(self.cache_dir, "search")
        self.apps_dir = os.path.join(self.cache_dir, "apps")
        os.makedirs(self.search_dir, exist_ok=True)
        os.makedirs(self.apps_dir, exist_ok=True)

    def run(self, *, args: Dict[str, Any]) -> ToolResult:
        app_id = args.get("app_id") or args.get("appid")
        name = (args.get("name") or args.get("query") or "").strip()
        max_results = _safe_int(args.get("max_results"), default=5, min_value=1, max_value=10)
        cc = (args.get("cc") or "US").strip().upper()[:2]
        lang = (args.get("lang") or args.get("l") or "english").strip()
        include_details = args.get("include_details")
        if include_details is None:
            include_details = True
        include_details = bool(include_details)

        if app_id:
            try:
                details, cache_state = self._get_app_details(str(app_id), cc=cc, lang=lang)
            except Exception as e:
                err = f"Steam app lookup failed: {e}"
                return ToolResult(
                    tool_name="steam.game_lookup",
                    args=args,
                    status="error",
                    output=tool_output(status="error", provider="steam", confidence="verbatim", error=err),
                    error=err,
                )
            if details is None:
                err = "Steam app not found."
                return ToolResult(
                    tool_name="steam.game_lookup",
                    args=args,
                    status="error",
                    output=tool_output(status="error", provider="steam", confidence="verbatim", error=err),
                    error=err,
                )
            data = {"app": details}
            return ToolResult(
                tool_name="steam.game_lookup",
                args=args,
                status="ok",
                output=tool_output(
                    status="ok",
                    provider="steam",
                    confidence="verbatim",
                    cache={"app": cache_state},
                    data=data,
                ),
            )

        if not name:
            err = "Missing game name."
            return ToolResult(
                tool_name="steam.game_lookup",
                args=args,
                status="error",
                output=tool_output(status="error", provider="steam", confidence="verbatim", error=err),
                error=err,
            )

        try:
            search_data, search_cache = self._search(name, max_results=max_results, cc=cc, lang=lang)
        except Exception as e:
            err = f"Steam search failed: {e}"
            return ToolResult(
                tool_name="steam.game_lookup",
                args=args,
                status="error",
                output=tool_output(status="error", provider="steam", confidence="verbatim", error=err),
                error=err,
            )
        matches = search_data.get("matches", [])
        app_details = None
        app_cache = None
        if matches and include_details:
            top_id = str(matches[0].get("app_id") or "")
            if top_id:
                try:
                    app_details, app_cache = self._get_app_details(top_id, cc=cc, lang=lang)
                except Exception:
                    app_details = None
                    app_cache = "error"
        data = {
            "query": name,
            "matches": matches,
            "app": app_details,
        }
        cache = {"search": search_cache}
        if app_cache is not None:
            cache["app"] = app_cache
        return ToolResult(
            tool_name="steam.game_lookup",
            args=args,
            status="ok",
            output=tool_output(status="ok", provider="steam", confidence="verbatim", cache=cache, data=data),
        )

    def _search(self, name: str, *, max_results: int, cc: str, lang: str) -> tuple[Dict[str, Any], str]:
        cache_key = self._cache_key(f"search:{name}:{cc}:{lang}:{max_results}")
        cached = self._read_cache(self.search_dir, cache_key)
        if cached is not None:
            return cached, "hit"

        params = {
            "term": name,
            "cc": cc,
            "l": lang,
        }
        headers = {"User-Agent": "bob-turbotime/steam-lookup"}
        resp = requests.get(STORE_SEARCH_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        items = raw.get("items", []) if isinstance(raw, dict) else []
        matches = []
        for item in items[:max_results]:
            matches.append(_normalize_search_item(item))

        data = {"matches": matches, "total": raw.get("total")}
        self._write_cache(self.search_dir, cache_key, data)
        return data, "miss"

    def _get_app_details(self, app_id: str, *, cc: str, lang: str) -> tuple[Optional[Dict[str, Any]], str]:
        cache_key = self._cache_key(f"app:{app_id}:{cc}:{lang}")
        cached = self._read_cache(self.apps_dir, cache_key)
        if cached is not None:
            return cached, "hit"

        params = {
            "appids": app_id,
            "cc": cc,
            "l": lang,
        }
        headers = {"User-Agent": "bob-turbotime/steam-lookup"}
        resp = requests.get(APP_DETAILS_URL, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        payload = raw.get(str(app_id)) if isinstance(raw, dict) else None
        if not payload or not payload.get("success"):
            return None, "miss"
        details = _normalize_app_details(app_id, payload.get("data") or {})
        self._write_cache(self.apps_dir, cache_key, details)
        return details, "miss"

    def _cache_key(self, value: str) -> str:
        norm = value.strip().lower().encode("utf-8")
        return hashlib.sha256(norm).hexdigest()

    def _read_cache(self, cache_dir: str, key: str) -> Optional[Dict[str, Any]]:
        path = os.path.join(cache_dir, f"{key}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except Exception:
            return None
        ts = entry.get("_ts")
        data = entry.get("data")
        if not ts or data is None:
            return None
        try:
            if (time.time() - float(ts)) > self.ttl_seconds:
                return None
        except Exception:
            return None
        return data

    def _write_cache(self, cache_dir: str, key: str, data: Dict[str, Any]) -> None:
        path = os.path.join(cache_dir, f"{key}.json")
        entry = {"_ts": time.time(), "data": data}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)


def _normalize_search_item(item: Dict[str, Any]) -> Dict[str, Any]:
    app_id = item.get("id")
    return {
        "app_id": app_id,
        "name": item.get("name"),
        "price": item.get("price"),
        "release_date": item.get("release_date"),
        "metascore": item.get("metascore"),
        "platforms": item.get("platforms"),
        "tiny_image": item.get("tiny_image"),
        "store_url": f"https://store.steampowered.com/app/{app_id}/" if app_id else None,
    }


def _normalize_app_details(app_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "app_id": int(app_id) if str(app_id).isdigit() else app_id,
        "name": data.get("name"),
        "short_description": data.get("short_description"),
        "release_date": (data.get("release_date") or {}).get("date"),
        "developers": data.get("developers"),
        "publishers": data.get("publishers"),
        "genres": [g.get("description") for g in (data.get("genres") or []) if g.get("description")],
        "categories": [c.get("description") for c in (data.get("categories") or []) if c.get("description")],
        "is_free": data.get("is_free"),
        "price": (data.get("price_overview") or {}).get("final_formatted"),
        "metacritic": (data.get("metacritic") or {}).get("score"),
        "recommendations": (data.get("recommendations") or {}).get("total"),
        "platforms": data.get("platforms"),
        "header_image": data.get("header_image"),
        "website": data.get("website"),
        "steam_url": f"https://store.steampowered.com/app/{app_id}/",
    }


def _safe_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        num = int(value)
    except Exception:
        return default
    return max(min_value, min(max_value, num))
