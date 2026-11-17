import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Secrets that can appear in URLs or messages (Adzuna puts app_key in the query string): masked in every log line.
REDACT = re.compile(r"(app_key|app_id|api_key|apikey|access_token|token|password|secret)=[^&\s\"']+", re.I)


def redact(text: str) -> str:
    return REDACT.sub(lambda m: f"{m.group(1)}=***", text)


_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        out.update({k: redact(v) if isinstance(v, str) else v for k, v in record.__dict__.items() if k not in _STD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True  # replaced by request middleware
    quiet_http_loggers()


def quiet_http_loggers() -> None:
    """httpx logs every request URL at INFO, which would print API keys sent as query parameters."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
