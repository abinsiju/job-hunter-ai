from pathlib import Path
from typing import Protocol

from app.core.config import settings


class Storage(Protocol):
    def save(self, key: str, data: bytes) -> str: ...
    def load(self, key: str) -> bytes: ...


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def save(self, key: str, data: bytes) -> str:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def load(self, key: str) -> bytes:
        return (self.root / key).read_bytes()


def get_storage() -> Storage:
    """Swap in an S3-compatible implementation when STORAGE_BACKEND changes."""
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_local_path)
    raise ValueError(f"Unknown STORAGE_BACKEND: {settings.storage_backend}")
