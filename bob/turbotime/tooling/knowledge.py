from __future__ import annotations

import html
import re
from typing import Any, Dict, List
from urllib.parse import parse_qs, unquote, urlparse

import requests

from bob.turbotime.tooling.base import ToolResult, tool_output

DDG_HTML_URL = "https://duckduckgo.com/html/"

DEFAULT_SOURCES = {
    "ign.com": "IGN",
    "gamefaqs.gamespot.com": "GameFAQs",
    "mtggoldfish.com": "MTGGoldfish",
}


class KnowledgeSearchTool:
    def run(self, *, args: Dict[str, Any]) -> ToolResult:
        query = (args.get("query") or args.get("q") or "").strip()
        if not query:
            err = "Missing query."
            return ToolResult(
                tool_name="game.knowledge_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="duckduckgo_html", confidence="summarized", error=err),
                error=err,
            )

        max_results = _safe_int(args.get("max_results"), default=5, min_value=1, max_value=10)
        sources = args.get("sources")
        source_domains = _normalize_sources(sources) or list(DEFAULT_SOURCES.keys())

        filter_query = " ".join([f"site:{d}" for d in source_domains])
        full_query = f"{query} {filter_query}".strip()

        try:
            headers = {"User-Agent": "bob-turbotime/knowledge-search"}
            resp = requests.get(DDG_HTML_URL, params={"q": full_query}, headers=headers, timeout=15)
            resp.raise_for_status()
            results = _parse_ddg_results(resp.text, source_domains, max_results)
        except Exception as e:
            err = f"Knowledge search failed: {e}"
            return ToolResult(
                tool_name="game.knowledge_search",
                args=args,
                status="error",
                output=tool_output(status="error", provider="duckduckgo_html", confidence="summarized", error=err),
                error=err,
            )
        data = {
            "query": query,
            "filtered_query": full_query,
            "sources": source_domains,
            "results": results,
            "result_count": len(results),
        }
        if not results:
            data["result_status"] = "no_structured_results"
            data["fallback_suggestions"] = ["combat", "progression", "bosses"]
            return ToolResult(
                tool_name="game.knowledge_search",
                args=args,
                status="partial_success",
                output=tool_output(
                    status="partial_success",
                    provider="duckduckgo_html",
                    confidence="summarized",
                    data=data,
                ),
            )
        return ToolResult(
            tool_name="game.knowledge_search",
            args=args,
            status="ok",
            output=tool_output(
                status="ok",
                provider="duckduckgo_html",
                confidence="summarized",
                data=data,
            ),
        )


def _normalize_sources(sources: Any) -> List[str]:
    if not sources:
        return []
    if isinstance(sources, str):
        return [sources]
    if isinstance(sources, list):
        out = []
        for s in sources:
            if isinstance(s, str) and s.strip():
                out.append(s.strip())
        return out
    return []


def _parse_ddg_results(html_text: str, allowed_domains: List[str], max_results: int) -> List[Dict[str, Any]]:
    links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</span>', html_text, re.S)

    results: List[Dict[str, Any]] = []
    for idx, (raw_url, raw_title) in enumerate(links):
        url = _decode_ddg_url(raw_url)
        if not url:
            continue
        domain = urlparse(url).netloc.lower()
        if not _domain_allowed(domain, allowed_domains):
            continue
        title = _strip_tags(raw_title)
        snippet = _strip_tags(snippets[idx]) if idx < len(snippets) else ""
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": _label_for_domain(domain),
                "domain": domain,
            }
        )
        if len(results) >= max_results:
            break
    return results


def _decode_ddg_url(raw_url: str) -> str:
    if "uddg=" in raw_url:
        parsed = urlparse(raw_url)
        qs = parse_qs(parsed.query)
        target = qs.get("uddg")
        if target:
            return unquote(target[0])
    return raw_url


def _strip_tags(text: str) -> str:
    cleaned = re.sub(r"<.*?>", "", text or "")
    return html.unescape(cleaned).strip()


def _domain_allowed(domain: str, allowed: List[str]) -> bool:
    for root in allowed:
        root = root.lower().strip()
        if not root:
            continue
        if domain == root or domain.endswith("." + root):
            return True
    return False


def _label_for_domain(domain: str) -> str:
    for root, label in DEFAULT_SOURCES.items():
        if domain == root or domain.endswith("." + root):
            return label
    return domain


def _safe_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        num = int(value)
    except Exception:
        return default
    return max(min_value, min(max_value, num))
