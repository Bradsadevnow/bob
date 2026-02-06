from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from bob.tools.sandbox import ToolSandbox
from bob.turbotime.tooling.base import ToolResult, ToolRunner
from bob.turbotime.tooling.knowledge import KnowledgeSearchTool
from bob.turbotime.tooling.news import NewsHeadlineSearchTool
from bob.turbotime.tooling.scryfall import ScryfallLookupTool
from bob.turbotime.tooling.steam import SteamGameLookupTool


@dataclass(frozen=True)
class ToolSpec:
    canonical_name: str
    public_name: str
    aliases: tuple[str, ...]
    tool: ToolRunner


class ToolRegistry:
    def __init__(self, *, sandbox: ToolSandbox, runtime_dir: str) -> None:
        self.sandbox = sandbox

        scryfall_cache = os.path.join(runtime_dir, "scryfall_cache")
        steam_cache = os.path.join(runtime_dir, "steam_cache")
        news_cache = os.path.join(runtime_dir, "news_cache")

        self._specs: list[ToolSpec] = [
            ToolSpec(
                canonical_name="SCRYFALL_LOOKUP",
                public_name="scryfall.lookup",
                aliases=("SCRYFALL_LOOKUP", "scryfall.lookup", "scryfall"),
                tool=ScryfallLookupTool(cache_dir=scryfall_cache),
            ),
            ToolSpec(
                canonical_name="STEAM_GAME_LOOKUP",
                public_name="steam.game_lookup",
                aliases=("STEAM_GAME_LOOKUP", "steam.game_lookup", "steam"),
                tool=SteamGameLookupTool(cache_dir=steam_cache),
            ),
            ToolSpec(
                canonical_name="GAME_KNOWLEDGE_SEARCH",
                public_name="game.knowledge_search",
                aliases=("GAME_KNOWLEDGE_SEARCH", "game.knowledge_search", "knowledge"),
                tool=KnowledgeSearchTool(),
            ),
            ToolSpec(
                canonical_name="NEWS_HEADLINE_SEARCH",
                public_name="news.headline_search",
                aliases=("NEWS_HEADLINE_SEARCH", "news.headline_search", "news"),
                tool=NewsHeadlineSearchTool(cache_dir=news_cache),
            ),
        ]

        self._specs_by_canonical: Dict[str, ToolSpec] = {spec.canonical_name: spec for spec in self._specs}
        self._alias_map: Dict[str, str] = {}
        for spec in self._specs:
            for name in (spec.canonical_name, spec.public_name, *spec.aliases):
                self._alias_map[_normalize_tool_name(name)] = spec.canonical_name

        self.allowed_tools = set(self._specs_by_canonical.keys())

    @property
    def public_tools(self) -> list[str]:
        return [spec.public_name for spec in self._specs]

    def resolve_public_name(self, name: str) -> Optional[str]:
        canonical = self._resolve(name)
        if not canonical:
            return None
        spec = self._specs_by_canonical.get(canonical)
        return spec.public_name if spec else None

    def resolve_allowed(self, names: Iterable[str]) -> tuple[set[str], list[str]]:
        canonical_set: set[str] = set()
        public_names: list[str] = []
        for name in names:
            canonical = self._resolve(name)
            if not canonical or canonical in canonical_set:
                continue
            canonical_set.add(canonical)
            spec = self._specs_by_canonical.get(canonical)
            if spec:
                public_names.append(spec.public_name)
        return canonical_set, public_names

    def run(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        allowed_tools: Optional[Iterable[str]] = None,
        bypass_sandbox: bool = False,
    ) -> ToolResult:
        canonical = self._resolve(tool_name)
        if not canonical:
            return ToolResult(tool_name=tool_name or "UNKNOWN", args=args, status="error", error="Tool not allowlisted.")

        if allowed_tools is not None:
            allowed_set = {self._resolve(n) for n in allowed_tools}
            allowed_set = {n for n in allowed_set if n}
            if canonical not in allowed_set:
                return ToolResult(
                    tool_name=self._specs_by_canonical[canonical].public_name,
                    args=args,
                    status="error",
                    error="Tool not enabled.",
                )
        else:
            if canonical not in self.allowed_tools:
                return ToolResult(
                    tool_name=self._specs_by_canonical[canonical].public_name,
                    args=args,
                    status="error",
                    error="Tool not allowlisted.",
                )

        if not self.sandbox.enabled and not bypass_sandbox:
            return ToolResult(
                tool_name=self._specs_by_canonical[canonical].public_name,
                args=args,
                status="error",
                error="Tool sandbox disabled.",
            )

        tool = self._specs_by_canonical[canonical].tool
        result = tool.run(args=args)
        result.tool_name = self._specs_by_canonical[canonical].public_name
        return result

    def _resolve(self, name: str | None) -> Optional[str]:
        if not name:
            return None
        return self._alias_map.get(_normalize_tool_name(name))


def _normalize_tool_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def parse_tool_requests(text: str, *, limit: int = 2) -> List[Dict[str, Any]]:
    if not text:
        return []

    marker = "TOOL REQUESTS:"
    if marker not in text:
        return []

    tail = text.split(marker, 1)[1]
    lines = [ln.rstrip() for ln in tail.splitlines()]

    out: List[Dict[str, Any]] = []
    in_block = False
    current: Dict[str, Any] = {}

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.upper().startswith("- NONE"):
            break
        if line.startswith("=== TOOL REQUEST ==="):
            in_block = True
            current = {}
            continue
        if line.startswith("STOP"):
            if current.get("tool"):
                out.append(current)
                if len(out) >= max(1, int(limit)):
                    break
            in_block = False
            current = {}
            continue
        if not in_block:
            continue

        if line.upper().startswith("TOOL:"):
            current["tool"] = line.split(":", 1)[1].strip()
            continue
        if line.upper().startswith("ARGS:"):
            args_raw = line.split(":", 1)[1].strip()
            try:
                current["args"] = json.loads(args_raw)
            except Exception:
                current["args"] = {}
                current["error"] = "ARGS parse error"
            continue
        if line.upper().startswith("PURPOSE:"):
            current["purpose"] = line.split(":", 1)[1].strip()
            continue
        if line.upper().startswith("EXPECTS:"):
            current["expects"] = line.split(":", 1)[1].strip()
            continue

    return out


def format_tool_results(results: List[ToolResult]) -> str:
    if not results:
        return ""
    lines = ["=== TOOL RESULTS (NON-AUTHORITATIVE) ==="]
    for r in results:
        payload = r.to_dict()
        lines.append(json.dumps(payload, ensure_ascii=False))
    return "\n".join(lines)
