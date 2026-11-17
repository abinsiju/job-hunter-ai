import logging

import httpx

from app.core.config import Settings

log = logging.getLogger("jobhunter.ai")


class OllamaProvider:
    def __init__(self, s: Settings):
        self._s = s
        self._client = httpx.AsyncClient(base_url=s.ai_base_url, timeout=300)
        self.stats = {"calls": 0, "errors_5xx": 0}

    async def chat(self, system: str, user: str, *, json_mode: bool = False, temperature: float | None = None,
                   json_schema: dict | None = None) -> str:
        payload = {
            "model": self._s.ai_chat_model,
            "stream": False,
            "think": False,
            "options": {"num_ctx": self._s.ai_num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_schema:  # structured outputs: the schema itself is passed as `format`
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"
        if temperature is not None:
            payload["options"]["temperature"] = temperature
        for attempt in range(4):  # Ollama 0.40.15 + qwen3:4b: JSON mode returns 500 intermittently (always at temperature 0); retries re-sample
            r = await self._client.post("/api/chat", json=payload)
            self.stats["calls"] += 1
            self.stats["errors_5xx"] += r.status_code >= 500
            if r.status_code < 500 or attempt == 3:
                break
            log.warning("ollama 5xx, retrying", extra={"status": r.status_code, "attempt": attempt})
        r.raise_for_status()
        return r.json()["message"]["content"]

    async def embed(self, text: str) -> list[float]:
        r = await self._client.post("/api/embed", json={"model": self._s.ai_embed_model, "input": text})
        r.raise_for_status()
        return r.json()["embeddings"][0]
