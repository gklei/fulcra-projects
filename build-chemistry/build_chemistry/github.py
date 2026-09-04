"""Bounded, unauthenticated access to GitHub's public starred API."""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .errors import GitHubAPIError, GitHubNotFoundError, GitHubRateLimitError, InvalidGitHubResponseError

MAX_STARS = 100


class GitHubClient:
    """Small public GitHub client. It deliberately has no token option."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.github.com",
        timeout: float = 10.0,
        page_size: int = 100,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self._page_size = page_size
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Accept": "application/vnd.github.star+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "build-chemistry/1",
            },
            follow_redirects=False,
        )

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch_starred(self, username: str, *, limit: int = MAX_STARS) -> list[dict[str, Any]]:
        """Fetch newest-first star representations, following no more pages than needed."""
        if not 1 <= limit <= MAX_STARS:
            raise ValueError("limit must be between 1 and 100")

        records: list[dict[str, Any]] = []
        page = 1
        while len(records) < limit:
            per_page = min(self._page_size, limit - len(records))
            response = self._client.get(
                f"/users/{username}/starred",
                params={"per_page": per_page, "page": page},
            )
            self._raise_for_status(response, username)
            try:
                payload = response.json()
            except ValueError as exc:
                raise InvalidGitHubResponseError("GitHub returned invalid JSON") from exc
            if not isinstance(payload, list):
                raise InvalidGitHubResponseError("GitHub starred response must be a JSON array")
            for item in payload:
                if not isinstance(item, dict):
                    raise InvalidGitHubResponseError("GitHub starred entries must be objects")
                records.append(item)
                if len(records) == limit:
                    break
            if len(payload) < per_page or not self._has_next_page(response):
                break
            page += 1
        return records

    @staticmethod
    def _has_next_page(response: httpx.Response) -> bool:
        return 'rel="next"' in response.headers.get("link", "")

    @staticmethod
    def _raise_for_status(response: httpx.Response, username: str) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 404:
            raise GitHubNotFoundError(username)
        remaining = response.headers.get("x-ratelimit-remaining")
        try:
            error_body = response.json()
        except ValueError:
            error_body = None
        error_message = error_body.get("message", "") if isinstance(error_body, dict) else ""
        is_rate_limit = "rate limit" in error_message.lower()
        if response.status_code in (403, 429) and (
            remaining == "0" or response.status_code == 429 or is_rate_limit
        ):
            reset_at: datetime | None = None
            reset = response.headers.get("x-ratelimit-reset")
            if reset is not None:
                try:
                    reset_at = datetime.fromtimestamp(int(reset), tz=timezone.utc)
                except (ValueError, OverflowError):
                    pass
            if reset_at is None and response.headers.get("retry-after") is not None:
                try:
                    reset_at = datetime.now(timezone.utc) + timedelta(
                        seconds=int(response.headers["retry-after"])
                    )
                except (ValueError, OverflowError):
                    pass
            raise GitHubRateLimitError(reset_at)
        message = "request failed"
        if isinstance(error_body, dict) and isinstance(error_body.get("message"), str):
            message = error_body["message"]
        raise GitHubAPIError(response.status_code, message)