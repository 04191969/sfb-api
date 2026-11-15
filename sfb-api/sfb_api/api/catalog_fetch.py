"""Polite catalog fetches. One identifiable user agent, and stop when a site says no.

A timeout is tried once. A 429 waits for Retry-After, then stops the host.
A missing page is skipped so the next vehicle can still be read.
"""

import time

import httpx

from .catalog_route import fetch_decision, request_decision, retry_wait

USER_AGENT = "SFBCatalogBot/1.0 (+https://bright-web-workshop-37.lovable.app)"


class CatalogBlocked(Exception):
    """The site refused the request. The crawl should stop."""

    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__(f"{status} {url}")


class CatalogMissing(Exception):
    """This page is gone or failed. Count it as rejected and continue."""

    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__(f"{status} {url}")


def fetch_catalog(url, timeout=20, pause=None, get=None, headers=None, retry_rate_limit=True):
    pause = pause or time.sleep
    get = get or httpx.get
    sent = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    if headers:
        sent.update(headers)
    last_error = None
    for attempt in range(2):
        try:
            response = get(
                url,
                headers=sent,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.RequestError as exc:
            last_error = exc
            if request_decision(attempt) == "retry":
                pause(2)
                continue
            raise CatalogMissing(0, url) from exc
        decision = fetch_decision(
            response.status_code,
            attempt if retry_rate_limit else 1,
        )
        if decision == "retry":
            pause(retry_wait(response.headers.get("Retry-After")))
            continue
        if decision == "stop":
            raise CatalogBlocked(response.status_code, url)
        if decision == "skip":
            raise CatalogMissing(response.status_code, url)
        return response.text
    raise CatalogMissing(getattr(last_error, "status", 0) or 0, url)
