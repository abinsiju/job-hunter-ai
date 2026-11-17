"""An append-only record of what the assistant observed and did. Personal values are NOT written here: only labels, states and sources."""
import json
from datetime import datetime, timezone
from pathlib import Path


class Audit:
    def __init__(self, path: Path | None):
        self.path, self.events = path, []
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **data) -> None:
        ev = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **data}
        self.events.append(ev)
        if self.path:
            with self.path.open("a") as f:
                f.write(json.dumps(ev, default=str) + "\n")
