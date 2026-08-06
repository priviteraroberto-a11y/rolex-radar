"""HTTP educato: rate limit, retry, rispetto di robots.txt."""
from __future__ import annotations

import logging
import time
import urllib.robotparser as robotparser
from typing import Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger("radar.fetch")


class Fetcher:
    def __init__(self, cfg: dict):
        self.timeout = cfg.get("timeout", 25)
        self.retries = cfg.get("retries", 2)
        self.delay = cfg.get("delay_between_requests", 2.0)
        self.respect_robots = cfg.get("respect_robots", True)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": cfg.get("user_agent", "Mozilla/5.0"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
        })
        self._robots: dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._last_request = 0.0

    # -- robots ---------------------------------------------------------------

    def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            self._robots[origin] = self._read_robots(origin)
        rp = self._robots[origin]
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.session.headers["User-Agent"], url)
        except Exception:
            return True

    def _read_robots(self, origin: str):
        """robotparser.read() non ha timeout: lo facciamo noi con requests."""
        try:
            r = self.session.get(f"{origin}/robots.txt", timeout=min(10, self.timeout))
        except requests.RequestException:
            return None                          # irraggiungibile → non blocchiamo
        if r.status_code != 200 or not r.text.strip():
            return None
        rp = robotparser.RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp

    # -- get ------------------------------------------------------------------

    def get(self, url: str) -> tuple[Optional[str], str]:
        """Ritorna (html, motivo). html None significa fallimento."""
        if not self._allowed(url):
            return None, "bloccato da robots.txt"

        last_error = "sconosciuto"
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if r.status_code == 200:
                    if _looks_like_bot_wall(r.text):
                        return None, "anti-bot (challenge page)"
                    return r.text, "ok"
                if r.status_code in (403, 429, 503):
                    last_error = f"HTTP {r.status_code} (probabile anti-bot)"
                    time.sleep(3 * (attempt + 1))
                    continue
                last_error = f"HTTP {r.status_code}"
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(2 * (attempt + 1))
        return None, last_error

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()


_BOT_WALL_MARKERS = (
    "just a moment", "checking your browser", "cf-browser-verification",
    "enable javascript and cookies", "datadome", "captcha-delivery",
    "px-captcha", "access denied", "request unsuccessful",
)


def _looks_like_bot_wall(html: str) -> bool:
    low = html[:5000].lower()
    return any(m in low for m in _BOT_WALL_MARKERS) and len(html) < 60000
