from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


def _norm_path(p: str) -> Path:
    return Path(p).expanduser().resolve()


@dataclass(frozen=True)
class ToolSandbox:
    """
    Per-session tool sandbox.

    Contract:
    - If `enabled` is False: no file/code tools may be used.
    - If enabled: file access is constrained to allowlisted roots.
    - All tool use should be logged by the orchestrator (not handled here).
    """

    enabled: bool
    allowed_roots: tuple[Path, ...]

    @staticmethod
    def disabled() -> "ToolSandbox":
        return ToolSandbox(enabled=False, allowed_roots=tuple())

    @staticmethod
    def enabled_with_roots(roots: Iterable[str]) -> "ToolSandbox":
        normalized = []
        for r in roots:
            if not r:
                continue
            try:
                normalized.append(_norm_path(r))
            except Exception:
                continue
        # de-dupe while preserving order
        out = []
        seen = set()
        for p in normalized:
            s = str(p)
            if s in seen:
                continue
            seen.add(s)
            out.append(p)
        return ToolSandbox(enabled=True, allowed_roots=tuple(out))

    def check_path(self, path: str) -> None:
        """
        Raises PermissionError if path is not allowed under sandbox settings.
        """
        if not self.enabled:
            raise PermissionError("Tool sandbox disabled for this session")

        target = _norm_path(path)

        for root in self.allowed_roots:
            try:
                target.relative_to(root)
                return
            except Exception:
                continue

        roots = ", ".join(str(r) for r in self.allowed_roots) or "(none)"
        raise PermissionError(f"Path not allowlisted: {target}. Allowed roots: {roots}")


def parse_allowed_roots(env_value: Optional[str]) -> list[str]:
    """
    Parse comma-separated allowlist roots from env.
    """
    if not env_value:
        return []
    parts = [p.strip() for p in env_value.split(",")]
    return [p for p in parts if p]

