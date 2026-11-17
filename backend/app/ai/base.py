from typing import Protocol


class AIProvider(Protocol):
    """Contract every inference backend implements; the app depends only on this."""

    async def chat(self, system: str, user: str, *, json_mode: bool = False, temperature: float | None = None,
                   json_schema: dict | None = None) -> str: ...
    async def embed(self, text: str) -> list[float]: ...
