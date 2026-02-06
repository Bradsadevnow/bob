from __future__ import annotations

import hashlib
import html
import json
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

import requests

from bob.turbotime.tooling.base import ToolResult, tool_output

GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


class NewsHeadlineSearchTool:
    def __init__(self, *, cache_dir: str, ttl_seconds: int = 60 * 30) -> None:
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        os.makedirs(self.cache_dir, exist_ok=True)

    def run(self, *, args: Dict[str, Any]) -> ToolResult:
        hop_id = (args.get("hop_id") or args.get("hop_token") or "").strip()
        if hop_id:
            return self._follow_link(hop_id=hop_id, args=args)

        if args.get("link") or args.get("url"):
            err = "Link follow requires hop_id from RSS results."
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )

        query = (args.get("query") or args.get("q") or "").strip()
        if not query:
            err = "Missing query."
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )

        max_results = _safe_int(args.get("max_results"), default=10, min_value=1, max_value=20)
        cache_key = self._cache_key(query)
        cached = self._read_cache(cache_key)
        if cached is not None:
            items = cached[:max_results]
            items = self._attach_hop_ids(items, query=query)
            data = {"query": query, "items": items}
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="ok",
                output=tool_output(
                    status="ok",
                    provider="google_news_rss",
                    confidence="verbatim",
                    cache="hit",
                    data=data,
                ),
            )

        try:
            params = {
                "q": query,
                "hl": "en-US",
                "gl": "US",
                "ceid": "US:en",
            }
            headers = {"User-Agent": "bob-turbotime/news-headlines"}
            resp = requests.get(GOOGLE_NEWS_RSS_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
            items = _parse_rss(resp.text)
        except Exception as e:
            err = f"News search failed: {e}"
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )
        self._write_cache(cache_key, items)
        items = self._attach_hop_ids(items[:max_results], query=query)
        data = {"query": query, "items": items}
        return ToolResult(
            tool_name="news.headline_search",
            args=args,
            status="ok",
            output=tool_output(
                status="ok",
                provider="google_news_rss",
                confidence="verbatim",
                cache="miss",
                data=data,
            ),
        )

    def _attach_hop_ids(self, items: List[Dict[str, Any]], *, query: str) -> List[Dict[str, Any]]:
        now_ts = time.time()
        registry = self._load_hop_registry()
        registry = self._prune_hop_registry(registry, now_ts=now_ts)

        out: List[Dict[str, Any]] = []
        for item in items:
            link = (item.get("link") or "").strip()
            if not link:
                out.append(item)
                continue
            hop_id = f"hop_{uuid.uuid4().hex}"
            registry[hop_id] = {
                "link": link,
                "query": query,
                "created_ts": now_ts,
                "used": False,
            }
            item_copy = dict(item)
            item_copy["hop_id"] = hop_id
            out.append(item_copy)

        self._write_hop_registry(registry)
        return out

    def _follow_link(self, *, hop_id: str, args: Dict[str, Any]) -> ToolResult:
        now_ts = time.time()
        registry = self._load_hop_registry()
        registry = self._prune_hop_registry(registry, now_ts=now_ts)

        entry = registry.get(hop_id)
        if not entry:
            err = "Hop ID not recognized or expired."
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )
        if entry.get("used"):
            err = "Link hop already used (thread locked)."
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )

        link = str(entry.get("link") or "").strip()
        if not link:
            err = "Hop ID missing link."
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )

        max_chars = _safe_int(args.get("max_chars"), default=1200, min_value=200, max_value=4000)
        try:
            headers = {"User-Agent": "bob-turbotime/news-link-follow"}
            resp = requests.get(link, headers=headers, timeout=15)
            resp.raise_for_status()
            html_text = resp.text
        except Exception as e:
            err = f"Link fetch failed: {e}"
            return ToolResult(
                tool_name="news.headline_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="google_news_rss", confidence="verbatim", error=err),
                error=err,
            )

        title = _extract_title(html_text)
        cleaned = _strip_html(html_text)
        excerpt = cleaned[:max_chars] + ("..." if len(cleaned) > max_chars else "")

        entry["used"] = True
        entry["used_at_ts"] = now_ts
        registry[hop_id] = entry
        self._write_hop_registry(registry)

        data = {
            "hop_id": hop_id,
            "link": link,
            "title": title,
            "excerpt": excerpt,
            "thread_locked": True,
        }
        return ToolResult(
            tool_name="news.headline_search",
            args=args,
            status="ok",
            output=tool_output(
                status="ok",
                provider="google_news_rss",
                confidence="summarized",
                data=data,
            ),
        )

    def _cache_key(self, query: str) -> str:
        norm = query.strip().lower().encode("utf-8")
        return hashlib.sha256(norm).hexdigest()

    def _cache_path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def _read_cache(self, key: str) -> Optional[List[Dict[str, Any]]]:
        path = self._cache_path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except Exception:
            return None
        ts = entry.get("_ts")
        items = entry.get("items")
        if not ts or items is None:
            return None
        try:
            if (time.time() - float(ts)) > self.ttl_seconds:
                return None
        except Exception:
            return None
        return items

    def _write_cache(self, key: str, items: List[Dict[str, Any]]) -> None:
        path = self._cache_path(key)
        entry = {"_ts": time.time(), "items": items}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)

    def _hop_registry_path(self) -> str:
        return os.path.join(self.cache_dir, "hop_registry.json")

    def _load_hop_registry(self) -> Dict[str, Any]:
        path = self._hop_registry_path()
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            return {}
        return {}

    def _write_hop_registry(self, registry: Dict[str, Any]) -> None:
        path = self._hop_registry_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)

    def _prune_hop_registry(self, registry: Dict[str, Any], *, now_ts: float) -> Dict[str, Any]:
        ttl = float(self.ttl_seconds)
        out: Dict[str, Any] = {}
        for hop_id, entry in registry.items():
            created = entry.get("created_ts")
            try:
                created_ts = float(created)
            except Exception:
                created_ts = None
            if created_ts is None:
                continue
            if (now_ts - created_ts) > ttl:
                continue
            out[str(hop_id)] = entry
        return out


def _parse_rss(xml_text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(xml_text)
    except Exception:
        return out

    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub_date = _text(item.find("pubDate"))
        source = _text(item.find("source"))
        out.append(
            {
                "title": title,
                "link": link,
                "published": pub_date,
                "source": source,
            }
        )
    return out


def _text(node) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _safe_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        num = int(value)
    except Exception:
        return default
    return max(min_value, min(max_value, num))


def _extract_title(html_text: str) -> str:
    if not html_text:
        return ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return " ".join(html.unescape(m.group(1)).split())


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    cleaned = re.sub(r"(?is)<(script|style).*?>.*?</\\1>", " ", html_text)
    cleaned = re.sub(r"(?s)<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    return " ".join(cleaned.split())
