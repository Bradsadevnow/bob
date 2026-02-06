from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI


class AIBroker:
    """
    Runs a single asyncio event loop in a background thread and executes all
    OpenAI async requests on it.

    Why:
    - Avoids per-turn asyncio.run(...) loop creation/teardown
    - Centralizes timeout behavior and reduces "hang forever" risk
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._client: Optional[AsyncOpenAI] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(target=self._run, name="ai-broker", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if not self._ready.is_set():
            raise RuntimeError("AIBroker failed to start event loop")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._client = AsyncOpenAI()
        self._ready.set()
        loop.run_forever()

    def responses_create_text(
        self,
        *,
        model: str,
        input: List[Dict[str, Any]],
        temperature: float,
        max_output_tokens: int,
        timeout: int,
    ) -> str:
        self.start()
        assert self._loop is not None
        assert self._client is not None

        async def _do() -> str:
            resp = await self._client.responses.create(
                model=model,
                input=input,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                timeout=timeout,
            )
            return resp.output_text

        fut = asyncio.run_coroutine_threadsafe(_do(), self._loop)
        try:
            return fut.result(timeout=timeout + 5)
        except Exception:
            fut.cancel()
            raise


_BROKER: Optional[AIBroker] = None


def get_broker() -> AIBroker:
    global _BROKER
    if _BROKER is None:
        _BROKER = AIBroker()
    return _BROKER
