import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from build_chemistry.fulcra_context import (
    ANNOTATION_SOURCE_PREFIX,
    FulcraContextAdapter,
    PROFILE_TYPE_NAME,
    STAR_TYPE_NAME,
    UnsafeShareError,
)
from build_chemistry.models import GitHubStarredRepository, ProjectionNote, StarProjection
from build_chemistry.lenses import regenerate_profile


class FakeFulcra:
    def __init__(self) -> None:
        self.owner_id = "owner-1"
        self.annotations: list[dict[str, Any]] = []
        self.tag_rows: list[dict[str, str]] = []
        self.records: dict[str, dict[str, Any]] = {}
        self.share_calls: list[dict[str, Any]] = []

    def get_fulcra_userid(self) -> str:
        return self.owner_id

    def annotations_catalog(self, fulcra_userid: str | None = None) -> list[dict[str, Any]]:
        assert fulcra_userid == self.owner_id
        return list(self.annotations)

    def create_annotation(self, **kwargs: Any) -> dict[str, Any]:
        self.create_tags(kwargs["tags"])
        row = {
            "id": str(UUID(int=len(self.annotations) + 1)),
            "name": kwargs["name"],
            "annotation_type": kwargs["annotation_type"],
            "deleted_at": None,
        }
        self.annotations.append(row)
        return row

    def create_tags(self, tag_names: list[str]) -> list[dict[str, str]]:
        result = []
        for name in tag_names:
            row = next((item for item in self.tag_rows if item["name"] == name), None)
            if row is None:
                row = {"id": str(UUID(int=100 + len(self.tag_rows))), "name": name}
                self.tag_rows.append(row)
            result.append(row)
        return result

    def tags(self) -> list[dict[str, str]]:
        return list(self.tag_rows)

    def record_data_type(
        self, data_type: str, records: list[dict[str, Any]], api_version: str
    ) -> dict[str, Any]:
        assert data_type == "MomentAnnotation"
        assert api_version == "v1alpha1"
        for record in records:
            self.records[record["id"]] = dict(record)
        return {"upload_id": f"upload-{len(self.records)}"}

    def fulcra_v1_api_path(
        self, path: str, params: dict[str, Any] | None = None
    ) -> bytes:
        assert params is not None
        type_uuid = path.rsplit("/", 1)[1]
        source = f"{ANNOTATION_SOURCE_PREFIX}{type_uuid}"
        start, end = params["start_time"], params["end_time"]
        rows = [
            row
            for row in self.records.values()
            if source in row["sources"]
            and start <= datetime.fromisoformat(row["recorded_at"]) < end
        ]
        return json.dumps(rows).encode()

    def create_datashare(self, **kwargs: Any) -> dict[str, Any]:
        self.share_calls.append(kwargs)
        return {"datashare_id": "share-1", **kwargs}


def projection() -> StarProjection:
    recorded_at = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    record = GitHubStarredRepository(
        record_id=UUID("4c008c14-5fdd-54ef-92f9-15f7c9fe4334"),
        recorded_at=recorded_at,
        note=ProjectionNote(
            github_username="octocat",
            repository_id=1,
            repository_node_id="R_1",
            full_name="Owner/Repo",
            html_url="https://github.com/Owner/Repo",
            description="A repository",
            primary_language="Python",
            topics=("local-first",),
            owner_login="Owner",
            is_fork=False,
            is_archived=False,
            observed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ),
        tags=(
            "build-chemistry-star",
            "star-projection-v1",
            "github-user:octocat",
            "repository:owner-repo",
            "language:python",
        ),
        sources=("github:stars:octocat", "build-chemistry:star-projector:v1"),
    )
    return StarProjection(github_username="octocat", records=(record,))


def test_types_tags_idempotent_write_and_time_scoped_read() -> None:
    client = FakeFulcra()
    adapter = FulcraContextAdapter(client)

    first_types = adapter.ensure_private_types()
    second_types = FulcraContextAdapter(client).ensure_private_types()
    assert first_types == second_types
    assert [row["name"] for row in client.annotations] == [STAR_TYPE_NAME, PROFILE_TYPE_NAME]

    expected = projection().records[0]
    adapter.write_stars(projection())
    adapter.write_stars(projection())
    assert len(client.records) == 1

    found = adapter.read_stars(
        expected.recorded_at - timedelta(seconds=1),
        expected.recorded_at + timedelta(seconds=1),
    )
    assert found == (expected,)
    assert adapter.read_stars(
        expected.recorded_at + timedelta(seconds=1),
        expected.recorded_at + timedelta(seconds=2),
    ) == ()
    stored = next(iter(client.records.values()))
    assert stored["recorded_at"] == expected.recorded_at.isoformat()
    assert stored["sources"][:2] == list(expected.sources)
    assert stored["sources"][-1].startswith(ANNOTATION_SOURCE_PREFIX)


def test_star_type_cannot_be_shared_or_smuggled_into_profile_share() -> None:
    client = FakeFulcra()
    adapter = FulcraContextAdapter(client)
    types = adapter.ensure_private_types()

    with pytest.raises(UnsafeShareError, match="records are private"):
        adapter.create_profile_share(
            name="unsafe", allowed_user_ids=["other"], data_types=[types.star]
        )
    with pytest.raises(UnsafeShareError):
        adapter.create_profile_share(
            name="mixed", allowed_user_ids=["other"], data_types=[types.profile, types.star]
        )
    assert client.share_calls == []

    result = adapter.create_profile_share(name="safe", allowed_user_ids=["b", "a", "a"])
    assert result["fulcra_data_types"] == [types.profile]
    assert result["allowed_user_ids"] == ["a", "b"]
    assert result["share_all_data"] is False


def test_read_window_requires_aware_ordered_timestamps() -> None:
    adapter = FulcraContextAdapter(FakeFulcra())
    aware = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="timezone"):
        adapter.read_stars(datetime(2025, 1, 1), aware)
    with pytest.raises(ValueError, match="before"):
        adapter.read_stars(aware, aware)


def test_profile_write_is_idempotent_and_strictly_read_back() -> None:
    client = FakeFulcra()
    adapter = FulcraContextAdapter(client)
    source = projection()
    star = source.records[0]
    adapter.write_stars(source)
    start = star.recorded_at - timedelta(seconds=1)
    end = star.recorded_at + timedelta(seconds=1)
    default = regenerate_profile(adapter, start, end, "default")
    profile = regenerate_profile(adapter, start, end, "pragmatic-builder")
    assert default.personality_name != profile.personality_name
    assert default.record_id != profile.record_id
    adapter.write_profile(profile)
    adapter.write_profile(profile)
    assert len(client.records) == 2  # one source star and one idempotent profile
    found = adapter.read_profiles(
        profile.generated_at - timedelta(seconds=1),
        profile.generated_at + timedelta(seconds=1),
    )
    assert found == (profile,)
    stored = client.records[str(profile.record_id)]
    assert stored["sources"][0].endswith(profile.source_fingerprint)
    assert stored["sources"][1].endswith("pragmatic-builder:v1")
    assert STAR_TYPE_NAME not in stored["note"]


@pytest.mark.live_fulcra
def test_live_fulcra_replay_is_idempotent_and_preserves_projection() -> None:
    """Bounded acceptance check: one public star, two real Context writes."""
    from app.fulcra_client import FulcraAuthError, get_fulcra_client
    from build_chemistry import project_public_stars

    try:
        client = get_fulcra_client()
    except FulcraAuthError as exc:
        pytest.skip(str(exc))

    adapter = FulcraContextAdapter(client)
    owner_id = client.get_fulcra_userid()
    live_projection = project_public_stars("octocat", owner_id=owner_id, limit=1)
    expected = live_projection.records[0]
    adapter.write_stars(live_projection)
    adapter.write_stars(live_projection)

    start = expected.recorded_at - timedelta(microseconds=1)
    end = expected.recorded_at + timedelta(microseconds=1)
    matching: list[GitHubStarredRepository] = []
    for _ in range(20):
        matching = [
            row
            for row in adapter.read_stars(start, end)
            if row.record_id == expected.record_id
        ]
        if matching:
            break
        time.sleep(1)

    assert len(matching) == 1
    actual = matching[0]
    assert actual.record_id == expected.record_id
    assert actual.recorded_at == expected.recorded_at
    assert actual.tags == expected.tags
    assert actual.sources == expected.sources
    assert actual.note.repository_id == expected.note.repository_id
    assert actual.note.full_name == expected.note.full_name
