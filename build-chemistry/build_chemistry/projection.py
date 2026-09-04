"""Normalization and deterministic identity for public GitHub stars."""

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from .errors import InsufficientPublicDataError, InvalidGitHubResponseError
from .github import GitHubClient, MAX_STARS
from .models import GitHubStarredRepository, ProjectionNote, StarProjection

RECORD_NAMESPACE = UUID("13eb0b43-44aa-5be4-bd7d-f39931b8d688")
USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise ValueError("invalid GitHub username")
    return normalized


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-") or "unknown"


def deterministic_record_id(owner_id: str, repository_id: int, starred_at: datetime) -> UUID:
    """Derive an idempotent record ID from owner and immutable event identity."""
    if starred_at.tzinfo is None or starred_at.utcoffset() is None:
        raise ValueError("starred_at must include a timezone")
    instant = starred_at.astimezone(timezone.utc).isoformat(timespec="microseconds")
    return uuid5(RECORD_NAMESPACE, f"{owner_id.strip().lower()}:{repository_id}:{instant}")


def _required(mapping: dict[str, Any], key: str, kind: type[Any]) -> Any:
    value = mapping.get(key)
    # Exact types matter at this external boundary. In particular, bool is a
    # subclass of int in Python and must not be accepted as a repository ID.
    if type(value) is not kind or (kind is str and not value):
        raise InvalidGitHubResponseError(f"GitHub star is missing valid {key!r}")
    return value


def _optional_string(mapping: dict[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if type(value) is not str:
        raise InvalidGitHubResponseError(f"GitHub star has invalid {key!r}; expected string or null")
    return value


def _topics(repo: dict[str, Any]) -> tuple[str, ...]:
    raw_topics = repo.get("topics", [])
    if type(raw_topics) is not list:
        raise InvalidGitHubResponseError("GitHub star has invalid 'topics'; expected an array")

    normalized: list[str] = []
    for value in raw_topics:
        if type(value) is not str or not value.strip():
            raise InvalidGitHubResponseError(
                "GitHub star has invalid 'topics'; entries must be non-empty strings"
            )
        topic = _slug(value)
        if topic not in normalized and len(normalized) < 10:
            normalized.append(topic)
    return tuple(normalized)


def normalize_star(
    item: dict[str, Any], *, github_username: str, owner_id: str, observed_at: datetime
) -> GitHubStarredRepository:
    try:
        starred_at = datetime.fromisoformat(_required(item, "starred_at", str).replace("Z", "+00:00"))
        repo = _required(item, "repo", dict)
        repository_id = _required(repo, "id", int)
        owner = _required(repo, "owner", dict)
        owner_login = _required(owner, "login", str)
        full_name = _required(repo, "full_name", str)
        language = _optional_string(repo, "language")
        topics = _topics(repo)
        note = ProjectionNote(
            github_username=github_username,
            repository_id=repository_id,
            repository_node_id=_required(repo, "node_id", str),
            full_name=full_name,
            html_url=_required(repo, "html_url", str),
            description=_optional_string(repo, "description"),
            primary_language=language,
            topics=topics,
            owner_login=owner_login,
            is_fork=_required(repo, "fork", bool),
            is_archived=_required(repo, "archived", bool),
            observed_at=observed_at,
        )
        tags = [
            "build-chemistry-star",
            "star-projection-v1",
            f"github-user:{github_username}",
            f"repository:{_slug(full_name)}",
        ]
        if language:
            tags.append(f"language:{_slug(language)}")
        tags.extend(f"topic:{topic}" for topic in topics)
        return GitHubStarredRepository(
            record_id=deterministic_record_id(owner_id, repository_id, starred_at),
            recorded_at=starred_at,
            note=note,
            tags=tuple(tags),
            sources=(f"github:stars:{github_username}", "build-chemistry:star-projector:v1"),
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise InvalidGitHubResponseError(f"invalid GitHub star: {exc}") from exc


def project_public_stars(
    username: str,
    *,
    owner_id: str | None = None,
    limit: int = MAX_STARS,
    client: GitHubClient | None = None,
    observed_at: datetime | None = None,
) -> StarProjection:
    """Fetch and normalize a bounded public projection.

    ``owner_id`` should be the Fulcra owner ID when preparing records to save.
    For transient public cards, the normalized GitHub username is a stable namespace.
    """
    normalized = normalize_username(username)
    instant = observed_at or datetime.now(timezone.utc)
    owns_client = client is None
    github = client or GitHubClient()
    try:
        raw = github.fetch_starred(normalized, limit=limit)
    finally:
        if owns_client:
            github.close()
    if not raw:
        raise InsufficientPublicDataError(normalized)
    identity_owner = owner_id.strip() if owner_id is not None else f"github:{normalized}"
    if not identity_owner:
        raise ValueError("owner_id cannot be empty")
    records = tuple(
        normalize_star(item, github_username=normalized, owner_id=identity_owner, observed_at=instant)
        for item in raw
    )
    return StarProjection(github_username=normalized, records=records)