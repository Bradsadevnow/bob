from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Literal, Optional, Tuple

import requests


@dataclass(frozen=True)
class ChatModel:
    base_url: str
    api_key: str
    model: str


class OpenAICompatClient:
    def __init__(self, cfg: ChatModel) -> None:
        self.cfg = cfg
        self._chat_url = f"{cfg.base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        *,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool,
        token_param: Literal["max_completion_tokens", "max_tokens"],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        payload[token_param] = max_tokens
        return payload

    def _post_with_token_fallback(
        self,
        *,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        timeout_s: int,
        stream: bool,
    ) -> Tuple[requests.Response, Literal["max_completion_tokens", "max_tokens"]]:
        """
        Some OpenAI-compatible servers (notably local ones) accept `max_tokens` but not
        `max_completion_tokens`. OpenAI itself supports `max_completion_tokens`.

        We try `max_completion_tokens` first (OpenAI-native), then fall back to `max_tokens`
        on 400-level "unknown field" style failures.
        """
        first: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
        second: Literal["max_completion_tokens", "max_tokens"] = "max_tokens"

        payload = self._build_payload(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            token_param=first,
        )
        resp = requests.post(
            self._chat_url,
            headers=self._headers(),
            json=payload,
            timeout=timeout_s,
            stream=stream,
        )

        if resp.status_code < 400:
            return resp, first

        # If the server doesn't like max_completion_tokens, retry once with max_tokens.
        # We avoid overfitting to any one error string, but keep it narrow to avoid masking real issues.
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            body = ""

        should_retry = resp.status_code in (400, 422) and "max_completion_tokens" in body
        if not should_retry:
            return resp, first

        try:
            resp.close()
        except Exception:
            pass

        payload2 = self._build_payload(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            token_param=second,
        )
        resp2 = requests.post(
            self._chat_url,
            headers=self._headers(),
            json=payload2,
            timeout=timeout_s,
            stream=stream,
        )
        return resp2, second

    def _raise_for_status(self, resp: requests.Response) -> None:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                detail = resp.text or ""
            except Exception:
                detail = ""
            if detail:
                raise requests.HTTPError(f"{exc}\nResponse body: {detail}") from None
            raise

    def chat_text(self, *, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, timeout_s: int) -> str:
        resp, _ = self._post_with_token_fallback(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            stream=False,
        )
        self._raise_for_status(resp)
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"] or ""
        except Exception:
            return json.dumps(data, ensure_ascii=False)

    def chat_text_stream(
        self, *, messages: List[Dict[str, Any]], temperature: float, max_tokens: int, timeout_s: int
    ) -> Generator[str, None, None]:
        resp, _ = self._post_with_token_fallback(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            stream=True,
        )
        with resp:
            resp.encoding = "utf-8"
            self._raise_for_status(resp)
            for raw_line in resp.iter_lines(decode_unicode=False):
                if not raw_line:
                    continue
                if isinstance(raw_line, bytes):
                    line = raw_line.decode("utf-8", errors="replace").strip()
                else:
                    line = str(raw_line).strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    return
                try:
                    evt = json.loads(data)
                    delta = evt.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except Exception:
                    continue
