"""
HTTP-утилиты: retry, rate-limit per host, ротация User-Agent.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests

from job_assistant.utils.logging import add_log

# ---------------------------------------------------------------------------
# User-Agent pool (вежливый парсинг)
# ---------------------------------------------------------------------------

USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) "
        "Gecko/20100101 Firefox/121.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) "
        "Gecko/20100101 Firefox/120.0"
    ),
]

# Минимальный интервал между запросами к одному host (сек)
DEFAULT_HOST_INTERVAL = {
    "api.hh.ru": 0.5,
    "hh.ru": 0.8,
    "remoteok.com": 0.6,
    "weworkremotely.com": 0.7,
}
_DEFAULT_INTERVAL = 0.5

_last_request_at: dict[str, float] = {}
_lock = threading.Lock()


def _pick_user_agent() -> str:
    return random.choice(USER_AGENTS)


def _host_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _wait_for_host(host: str, extra_delay: float = 0.0) -> None:
    """Глобальный rate-limit по host."""
    interval = DEFAULT_HOST_INTERVAL.get(host, _DEFAULT_INTERVAL) + extra_delay
    with _lock:
        now = time.monotonic()
        last = _last_request_at.get(host, 0.0)
        wait = interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        _last_request_at[host] = time.monotonic()


def request_with_retry(
    url: str,
    *,
    method: str = "GET",
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = 20,
    max_retries: int = 3,
    backoff: float = 1.5,
    rate_limit_sec: float = 0.0,
) -> requests.Response:
    """
    GET/POST с:
      - rate-limit по host
      - ротацией User-Agent
      - retry на 429/5xx и сетевых ошибках
    """
    host = _host_of(url)
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            _wait_for_host(host, extra_delay=rate_limit_sec if attempt == 1 else 0)

            hdrs = {
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "User-Agent": _pick_user_agent(),
                **(headers or {}),
            }
            # если caller передал свой UA — оставляем его, иначе ротация уже в hdrs
            if headers and "User-Agent" in headers:
                hdrs["User-Agent"] = headers["User-Agent"]
            else:
                hdrs["User-Agent"] = _pick_user_agent()

            resp = requests.request(
                method,
                url,
                params=params,
                headers=hdrs,
                timeout=timeout,
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                add_log(
                    "WARNING",
                    f"HTTP {resp.status_code} {host} (попытка {attempt}/{max_retries})",
                )
                time.sleep(backoff * attempt + random.uniform(0.1, 0.4))
                continue

            if resp.status_code == 403:
                add_log("WARNING", f"HTTP 403 Forbidden: {host} — возможна блокировка")
                # одна дополнительная попытка с другим UA
                if attempt < max_retries:
                    time.sleep(backoff * attempt)
                    continue

            resp.raise_for_status()
            return resp

        except requests.RequestException as e:
            last_exc = e
            add_log(
                "WARNING",
                f"Сеть/таймаут {host}: {type(e).__name__} (попытка {attempt}/{max_retries})",
            )
            time.sleep(backoff * attempt + random.uniform(0.05, 0.3))

    raise RuntimeError(
        f"Не удалось загрузить {host or url} после {max_retries} попыток: {last_exc}"
    )
