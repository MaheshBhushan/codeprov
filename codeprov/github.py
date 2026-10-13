"""GitHub REST evidence cache with bounded, same-origin pagination."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx


API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "codeprov-research"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, timeout=60, follow_redirects=False)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    @staticmethod
    def safe_url(url):
        url = urljoin(API + "/", url)
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "api.github.com"
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise GitHubError(f"Refusing non-GitHub pagination URL: {url}")
        return url

    def get(self, path, params=None):
        url = self.safe_url(path)
        url = str(httpx.URL(url, params=sorted(params.items()))) if params else url
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path = self.cache_dir / f"{key}.json"
        metadata_path = self.cache_dir / f"{key}.meta.json"
        if body_path.is_file() and metadata_path.is_file():
            metadata = json.loads(metadata_path.read_text())
            if metadata["url"] == url and metadata["status"] == 200:
                next_url = metadata.get("next")
                if next_url:
                    self.safe_url(next_url)
                return json.loads(body_path.read_bytes()), next_url, str(body_path)
        try:
            response = self.client.get(url)
        except httpx.HTTPError as error:
            raise GitHubError(f"GitHub request failed for {url}: {error}") from error
        body_path.write_bytes(response.content)
        next_url = response.links.get("next", {}).get("url")
        metadata = {"url": url, "status": response.status_code,
                    "headers": dict(response.headers), "next": next_url}
        metadata_path.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
        if response.status_code != 200:
            try:
                message = response.json().get("message", response.text)
            except (ValueError, AttributeError):
                message = response.text
            if response.status_code in (403, 429) and (
                response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0"
                or "rate limit" in str(message).lower() or response.headers.get("retry-after")
            ):
                reset = response.headers.get("x-ratelimit-reset", "unknown")
                retry = response.headers.get("retry-after", "unspecified")
                raise GitHubError(f"GitHub rate limit at {url}: {message}; reset Unix time={reset}, "
                                  f"Retry-After={retry}. Wait until reset; set GITHUB_TOKEN for authenticated "
                                  "public-API quota. Cached successful pages will be reused.")
            raise GitHubError(f"GitHub HTTP {response.status_code} at {url}: {message}; "
                              f"raw response: {body_path}")
        if next_url:
            self.safe_url(next_url)
        try:
            return response.json(), next_url, str(body_path)
        except ValueError as error:
            raise GitHubError(f"GitHub returned invalid JSON at {url}; raw response: {body_path}") from error

    def pages(self, path, params=None):
        """Yield unmodified complete page payloads and their raw evidence paths."""
        seen = set()
        while path:
            url = self.safe_url(path)
            marker = str(httpx.URL(url, params=sorted(params.items()))) if params else url
            if marker in seen:
                raise GitHubError(f"Cyclic GitHub pagination at {marker}")
            seen.add(marker)
            payload, next_url, raw_path = self.get(url, params)
            yield payload, raw_path
            path, params = next_url, None
