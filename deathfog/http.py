import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Http:
    """Tiny stdlib HTTP client. Sources only ever call get_json / get_bytes, so tests can swap in a fake."""

    def __init__(self, user_agent, timeout=20):
        self.user_agent = user_agent
        self.timeout = timeout

    def get_bytes(self, url, params=None, headers=None):
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
        req = Request(url, headers={"User-Agent": self.user_agent, **(headers or {})})
        for attempt in range(2):
            try:
                with urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except HTTPError as e:
                # NWS and others throttle with 429/503; one polite retry, then give up until next run.
                if e.code in (429, 503) and attempt == 0:
                    ra = e.headers.get("Retry-After", "")
                    time.sleep(min(float(ra), 10) if ra.replace(".", "", 1).isdigit() else 5)
                    continue
                raise HttpError(e.code, f"HTTP {e.code} for {url}") from e
            except URLError as e:
                raise HttpError(0, f"network error for {url}: {e.reason}") from e

    def get_json(self, url, params=None, headers=None):
        return json.loads(self.get_bytes(url, params, headers))
