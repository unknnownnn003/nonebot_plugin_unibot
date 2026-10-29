"""Keep signed file URLs and query credentials out of HTTP request diagnostics."""
import logging
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import httpcore

URL = re.compile(r"https?://[^\s'\"<>]+")
SECRET_FIELDS = {"authorization", "proxy-authorization", "x-api-key", "api_key", "token", "access_token",
                 "cookie", "set-cookie", "client_secret", "password"}


def field_name(value):
    return (value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)).lower()


def redact(value):
    if isinstance(value, httpx.URL):
        value = str(value)
    if isinstance(value, str):
        def sanitize(match):
            try:
                url = urlsplit(match.group())
                netloc = url.netloc.rsplit("@", 1)[-1]
                query = urlencode([(key, "REDACTED") for key, _ in parse_qsl(url.query, keep_blank_values=True)])
                return urlunsplit((url.scheme, netloc, url.path, query, "REDACTED" if url.fragment else ""))
            except ValueError:
                return "REDACTED_URL"
        return URL.sub(sanitize, value)
    if isinstance(value, bytes):
        return redact(value.decode("utf-8", errors="replace")).encode("utf-8") if b"http" in value else value
    if isinstance(value, tuple):
        if len(value) == 2 and field_name(value[0]) in SECRET_FIELDS:
            return (value[0], b"REDACTED" if isinstance(value[1], bytes) else "REDACTED")
        return tuple(redact(item) for item in value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: "REDACTED" if field_name(key) in SECRET_FIELDS else redact(item) for key, item in value.items()}
    return value


class QueryRedactor(logging.Filter):
    _unibot_privacy_filter = True
    def filter(self, record):
        # httpcore Trace preformats wire headers into strings, losing their
        # structure. Keep those raw DEBUG traces out of diagnostics entirely.
        if record.name.startswith("httpcore") and record.levelno <= logging.DEBUG:
            return False
        record.msg = redact(record.msg)
        record.args = redact(record.args)
        return True


def install():
    # These are library diagnostics only. Requests and their URLs are unchanged.
    names = {"httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.http2", "httpcore.proxy"}
    names.update(name for name in logging.Logger.manager.loggerDict if name.startswith("httpcore."))
    for name in names:
        logger = logging.getLogger(name)
        if not any(getattr(item, "_unibot_privacy_filter", False) for item in logger.filters):
            logger.addFilter(QueryRedactor())


def client(**kwargs):
    value = httpx.AsyncClient(**kwargs)
    # Constructing a transport may lazily register more httpcore loggers.
    install()
    return value


install()
