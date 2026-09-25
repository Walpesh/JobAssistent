"""
Browser-эмуляция для обхода блокировок HH при импорте по ссылке поиска.

Методы (по приоритету доступности):
  1. Эмуляция действий пользователя через Selenium (случайные паузы, scroll)
  2. undetected-chromedriver (если установлен)
  3. Playwright stealth (если установлен)

Прокси / TLS-отпечатки — опционально через env:
  HH_BROWSER_PROXY=http://user:pass@host:port
"""

from __future__ import annotations

import os
import random
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from job_assistant.utils.logging import add_log


def _page_url(original: str, page: int) -> str:
    parsed = urlparse(original)
    qs = parse_qs(parsed.query, keep_blank_values=False)
    qs["page"] = [str(page)]
    if "items_on_page" not in qs:
        qs["items_on_page"] = ["50"]
    pairs = [(k, v) for k, vals in qs.items() for v in vals]
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc,
            parsed.path,
            "",
            urlencode(pairs),
            "",
        )
    )


def _parse_cards_from_html(html: str, host: str) -> list[dict[str, Any]]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    soup = BeautifulSoup(html or "", "lxml")
    cards = soup.select('[data-qa="vacancy-serp__vacancy"]')
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(vid: str, title: str, href: str, company: str = "") -> None:
        if not vid or vid in seen:
            return
        seen.add(vid)
        if href.startswith("/"):
            href = f"https://{host}{href}"
        items.append(
            {
                "id": vid,
                "name": title,
                "alternate_url": href or f"https://{host}/vacancy/{vid}",
                "employer": {"name": company} if company else {},
                "snippet": {"requirement": ""},
                "_from_browser": True,
            }
        )

    for card in cards:
        a = (
            card.select_one('a[data-qa="serp-item__title"]')
            or card.select_one('a[href*="/vacancy/"]')
        )
        if not a:
            continue
        href = a.get("href") or ""
        m = re.search(r"/vacancy/(\d+)", href)
        if not m:
            continue
        company_el = card.select_one('[data-qa="vacancy-serp__vacancy-employer"]')
        add(
            m.group(1),
            a.get_text(" ", strip=True),
            href.split("?")[0],
            company_el.get_text(" ", strip=True) if company_el else "",
        )

    if not items:
        for a in soup.select('a[href*="/vacancy/"]'):
            href = a.get("href") or ""
            m = re.search(r"/vacancy/(\d+)", href)
            if not m:
                continue
            title = a.get_text(" ", strip=True)
            if title and len(title) >= 3:
                add(m.group(1), title, href.split("?")[0])

    return items


def _human_pause(a: float = 0.6, b: float = 1.8) -> None:
    time.sleep(random.uniform(a, b))


def _fetch_selenium(
    url: str,
    *,
    max_pages: int,
    should_stop: Optional[Callable[[], bool]],
    on_progress: Optional[Callable[[dict[str, Any]], None]],
    use_undetected: bool,
) -> list[dict[str, Any]]:
    host = urlparse(url).netloc or "hh.ru"
    proxy = os.environ.get("HH_BROWSER_PROXY") or os.environ.get("HTTPS_PROXY") or ""

    driver = None
    if use_undetected:
        try:
            import undetected_chromedriver as uc

            options = uc.ChromeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1365,900")
            if proxy:
                options.add_argument(f"--proxy-server={proxy}")
            driver = uc.Chrome(options=options, headless=True)
            add_log("INFO", "Browser HH: undetected-chromedriver")
        except Exception as e:
            add_log("WARNING", f"undetected-chromedriver недоступен: {e}")
            driver = None

    if driver is None:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1365,900")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        if proxy:
            options.add_argument(f"--proxy-server={proxy}")
        # attempt to reduce automation flags
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        driver = webdriver.Chrome(options=options)
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {
                    "source": (
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined})"
                    )
                },
            )
        except Exception:
            pass
        add_log("INFO", "Browser HH: selenium Chrome (user-action emulation)")

    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for page in range(max(1, max_pages)):
            if should_stop and should_stop():
                break
            page_url = _page_url(url, page)
            driver.get(page_url)
            _human_pause(1.2, 2.8)
            # эмуляция скролла
            try:
                for _ in range(random.randint(2, 5)):
                    driver.execute_script(
                        "window.scrollBy(0, arguments[0]);",
                        random.randint(200, 600),
                    )
                    _human_pause(0.3, 0.9)
            except Exception:
                pass
            html = driver.page_source or ""
            batch = _parse_cards_from_html(html, host)
            new = 0
            for it in batch:
                vid = str(it.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                all_items.append(it)
                new += 1
            if on_progress:
                on_progress(
                    {
                        "status": "running",
                        "page": page,
                        "pages": max_pages,
                        "found": len(all_items),
                        "fetched": len(all_items),
                        "page_new": new,
                        "mode": "browser",
                    }
                )
            add_log(
                "INFO",
                f"Browser HH page {page + 1}: +{new} (всего {len(all_items)})",
            )
            if new == 0:
                break
            _human_pause(0.8, 2.0)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    return all_items


def _fetch_playwright(
    url: str,
    *,
    max_pages: int,
    should_stop: Optional[Callable[[], bool]],
    on_progress: Optional[Callable[[dict[str, Any]], None]],
) -> list[dict[str, Any]]:
    from playwright.sync_api import sync_playwright

    host = urlparse(url).netloc or "hh.ru"
    proxy = os.environ.get("HH_BROWSER_PROXY") or ""
    proxy_cfg = {"server": proxy} if proxy else None

    all_items: list[dict[str, Any]] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1365, "height": 900},
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            proxy=proxy_cfg,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.new_page()
        add_log("INFO", "Browser HH: Playwright chromium")

        for page_i in range(max(1, max_pages)):
            if should_stop and should_stop():
                break
            page_url = _page_url(url, page_i)
            page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
            _human_pause(1.0, 2.5)
            for _ in range(random.randint(2, 4)):
                page.mouse.wheel(0, random.randint(250, 700))
                _human_pause(0.25, 0.7)
            html = page.content()
            batch = _parse_cards_from_html(html, host)
            new = 0
            for it in batch:
                vid = str(it.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                all_items.append(it)
                new += 1
            if on_progress:
                on_progress(
                    {
                        "status": "running",
                        "page": page_i,
                        "pages": max_pages,
                        "found": len(all_items),
                        "fetched": len(all_items),
                        "page_new": new,
                        "mode": "browser",
                    }
                )
            add_log(
                "INFO",
                f"Playwright HH page {page_i + 1}: +{new} (всего {len(all_items)})",
            )
            if new == 0:
                break
            _human_pause(0.7, 1.8)

        context.close()
        browser.close()

    return all_items


def fetch_search_via_browser(
    url: str,
    *,
    should_stop: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """
    Попробовать методы browser-эмуляции по порядку.
    """
    max_pages = max(1, min(int(max_pages), 40))
    errors: list[str] = []

    # Method 2: undetected-chromedriver
    try:
        items = _fetch_selenium(
            url,
            max_pages=max_pages,
            should_stop=should_stop,
            on_progress=on_progress,
            use_undetected=True,
        )
        if items:
            return items
    except Exception as e:
        errors.append(f"undetected: {e}")
        add_log("WARNING", f"Browser method undetected failed: {e}")

    # Method 1: plain selenium user-action emulation
    try:
        items = _fetch_selenium(
            url,
            max_pages=max_pages,
            should_stop=should_stop,
            on_progress=on_progress,
            use_undetected=False,
        )
        if items:
            return items
    except Exception as e:
        errors.append(f"selenium: {e}")
        add_log("WARNING", f"Browser method selenium failed: {e}")

    # Method 3-ish: Playwright (+ optional proxy via env)
    try:
        items = _fetch_playwright(
            url,
            max_pages=max_pages,
            should_stop=should_stop,
            on_progress=on_progress,
        )
        if items:
            return items
    except Exception as e:
        errors.append(f"playwright: {e}")
        add_log("WARNING", f"Browser method playwright failed: {e}")

    if errors:
        add_log("ERROR", "Все browser-методы исчерпаны: " + "; ".join(errors[:5]))
    return []
