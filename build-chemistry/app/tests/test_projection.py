from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from build_chemistry import (
    GitHubClient,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubStarredRepository,
    InsufficientPublicDataError,
    deterministic_record_id,
    project_public_stars,
)
from build_chemistry.errors import InvalidGitHubResponseError


OBSERVED_AT = datetime(2026, 1, 2, tzinfo=timezone.utc)


def star(number: int) -> dict[str, object]:
    return {
        "starred_at": f"2025-01-{number:02d}T12:00:00Z",
        "repo": {
            "id": number,
            "node_id": f"R_{number}",
            "full_name": f"Owner/Repo-{number}",
            "html_url": f"https://github.com/Owner/Repo-{number}",
            "description": "A repository",
            "language": "Type Script",
            "topics": ["Local First", "tools", "tools"],
            "owner": {"login": "Owner"},
            "fork": False,
            "archived": False,
        },
    }


def test_bounded_pagination_and_required_github_headers() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        headers = {"link": '<https://api.github.com/x?page=2>; rel="next"'} if page == 1 else {}
        payload = [star(1), star(2)] if page == 1 else [star(3)]
        return httpx.Response(200, json=payload, headers=headers)

    with GitHubClient(page_size=2, transport=httpx.MockTransport(handler)) as client:
        result = client.fetch_starred("octocat", limit=3)

    assert len(result) == 3
    assert len(requests) == 2
    assert requests[1].url.params["per_page"] == "1"
    assert requests[0].headers["accept"] == "application/vnd.github.star+json"
    assert "authorization" not in requests[0].headers


def test_normalizes_timestamp_tags_sources_and_deterministic_id() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[star(1)]))
    with GitHubClient(transport=transport) as client:
        first = project_public_stars(
            " OctoCat ", owner_id="fulcra-owner", client=client, observed_at=OBSERVED_AT
        )
    with GitHubClient(transport=transport) as client:
        second = project_public_stars(
            "octocat", owner_id="fulcra-owner", client=client, observed_at=OBSERVED_AT
        )

    record = first.records[0]
    assert record.recorded_at == datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    assert record.record_id == second.records[0].record_id
    assert record.note.github_username == "octocat"
    assert record.note.topics == ("local-first", "tools")
    assert "language:type-script" in record.tags
    assert "repository:owner-repo-1" in record.tags
    assert record.sources == (
        "github:stars:octocat",
        "build-chemistry:star-projector:v1",
    )


def test_identity_is_owner_scoped_and_timezone_canonical() -> None:
    instant = datetime.fromisoformat("2025-01-01T12:00:00+00:00")
    same_instant = datetime.fromisoformat("2025-01-01T07:00:00-05:00")
    assert deterministic_record_id("A", 1, instant) == deterministic_record_id("a", 1, same_instant)
    assert deterministic_record_id("a", 1, instant) != deterministic_record_id("b", 1, instant)


def test_sparse_and_empty_accounts_are_distinguished() -> None:
    sparse_transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[star(1)]))
    with GitHubClient(transport=sparse_transport) as client:
        assert len(project_public_stars("octocat", client=client).records) == 1

    empty_transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
    with GitHubClient(transport=empty_transport) as client:
        with pytest.raises(InsufficientPublicDataError):
            project_public_stars("octocat", client=client)


def test_rate_limit_and_not_found_errors() -> None:
    limited = httpx.MockTransport(
        lambda _request: httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1735732800"},
        )
    )
    with GitHubClient(transport=limited) as client:
        with pytest.raises(GitHubRateLimitError) as caught:
            client.fetch_starred("octocat")
    assert caught.value.reset_at == datetime.fromtimestamp(1735732800, tz=timezone.utc)

    missing = httpx.MockTransport(lambda _request: httpx.Response(404, json={"message": "Not Found"}))
    with GitHubClient(transport=missing) as client:
        with pytest.raises(GitHubNotFoundError):
            client.fetch_starred("not-a-real-user")


def test_invalid_external_data_and_strict_output_schema() -> None:
    malformed = httpx.MockTransport(
        lambda _request: httpx.Response(200, json=[{"repo": {"id": 1}}])
    )
    with GitHubClient(transport=malformed) as client:
        with pytest.raises(InvalidGitHubResponseError):
            project_public_stars("octocat", client=client)

    valid_transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[star(1)]))
    with GitHubClient(transport=valid_transport) as client:
        record = project_public_stars("octocat", client=client).records[0]
    with pytest.raises(ValidationError):
        GitHubStarredRepository.model_validate({**record.model_dump(), "secret": "not allowed"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", True),
        ("id", "1"),
        ("topics", None),
        ("topics", ["valid", 1]),
        ("topics", [f"topic-{index}" for index in range(10)] + [1]),
        ("language", 123),
        ("description", False),
    ],
)
def test_malformed_and_coercible_repository_fields_use_validation_error(
    field: str, value: object
) -> None:
    item = star(1)
    repo = item["repo"]
    assert isinstance(repo, dict)
    repo[field] = value
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[item]))

    with GitHubClient(transport=transport) as client:
        with pytest.raises(InvalidGitHubResponseError, match=field):
            project_public_stars("octocat", client=client)


def test_output_schema_does_not_coerce_repository_id() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[star(1)]))
    with GitHubClient(transport=transport) as client:
        record = project_public_stars("octocat", client=client).records[0]

    dumped = record.model_dump()
    dumped["note"]["repository_id"] = "1"
    with pytest.raises(ValidationError):
        GitHubStarredRepository.model_validate(dumped)


@pytest.mark.parametrize("limit", [0, 101])
def test_limit_cannot_escape_sample_bound(limit: int) -> None:
    with GitHubClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))) as client:
        with pytest.raises(ValueError, match="between 1 and 100"):
            client.fetch_starred("octocat", limit=limit)