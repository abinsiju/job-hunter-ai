from app.ai.base import AIProvider
from app.ai.ollama import OllamaProvider
from app.core.config import Settings, settings


def get_provider(s: Settings = settings) -> AIProvider:
    """Add new providers here (e.g. an OpenAI-compatible cloud endpoint) and set AI_PROVIDER."""
    if s.ai_provider == "ollama":
        return OllamaProvider(s)
    raise ValueError(f"Unknown AI_PROVIDER: {s.ai_provider}")
