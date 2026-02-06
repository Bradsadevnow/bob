from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass
class ToolResult:
    tool_name: str
    args: Dict[str, Any]
    status: str
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "args": dict(self.args or {}),
            "status": self.status,
            "output": self.output,
            "error": self.error,
        }


def tool_output(
    *,
    status: str,
    provider: str,
    confidence: str,
    data: Optional[Dict[str, Any]] = None,
    cache: Optional[Any] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "status": status,
        "source": "tool_output",
        "confidence": confidence,
        "provider": provider,
    }
    if cache is not None:
        out["cache"] = cache
    if data is not None:
        out["data"] = data
    if error:
        out["error"] = error
    return out


class ToolRunner(Protocol):
    def run(self, *, args: Dict[str, Any]) -> ToolResult: ...
