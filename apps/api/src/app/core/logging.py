"""Application logging configuration with defense-in-depth secret redaction."""

import logging
import re

_URI_CREDS_RE = re.compile(r"://[^\s/]+@")
_BEARER_RE = re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_KV_SECRET_RE = re.compile(
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|x-api-key|bearerToken|authorization|"
    r"access[_-]?token|refresh[_-]?token|client[_-]?secret|connectionUri|dsn|conninfo|"
    r"credential_encryption_keys)\s*[:=]\s*"
    r'(?:"[^"]*"|\'[^\']*\'|[^\s,;\}\]]+)',
    re.IGNORECASE,
)


def redact_sensitive_text(text: str) -> str:
    """Scrub common secret patterns from log strings and sanitized error messages."""
    redacted = _URI_CREDS_RE.sub("://[REDACTED]@", text)
    redacted = _BEARER_RE.sub("[REDACTED]", redacted)
    redacted = _KV_SECRET_RE.sub(r"\1=[REDACTED]", redacted)
    return redacted


class SensitiveDataFilter(logging.Filter):
    """Logging filter that redacts credentials, tokens, and URI userinfo."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_sensitive_text(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    redact_sensitive_text(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: redact_sensitive_text(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the DataBridge API logger with sensitive-data filtering."""
    logger = logging.getLogger("databridge.api")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        handler.setFormatter(formatter)
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
                handler.addFilter(SensitiveDataFilter())

    logger.propagate = False
    return logger
