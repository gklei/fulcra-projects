"""Stable, user-presentable errors from the GitHub projection boundary."""

from datetime import datetime


class ProjectionError(Exception):
    """Base class for expected projection failures."""


class GitHubAPIError(ProjectionError):
    """GitHub returned an unexpected response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(f"GitHub API returned {status_code}: {message}")


class GitHubNotFoundError(GitHubAPIError):
    """The username is unknown, or its public stars cannot be accessed."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(404, f"GitHub user {username!r} was not found or is not public")


class GitHubRateLimitError(GitHubAPIError):
    """The unauthenticated GitHub API allowance has been exhausted."""

    def __init__(self, reset_at: datetime | None) -> None:
        self.reset_at = reset_at
        detail = "rate limit exceeded"
        if reset_at is not None:
            detail += f"; retry after {reset_at.isoformat()}"
        super().__init__(403, detail)


class InsufficientPublicDataError(ProjectionError):
    """No public star events are available for a useful projection."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(f"GitHub user {username!r} has no visible starred repositories")


class InvalidGitHubResponseError(ProjectionError):
    """GitHub data did not satisfy the expected external contract."""