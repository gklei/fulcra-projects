from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from build_chemistry import (
    BuilderTasteProfile,
    extract_features,
    generate_profile,
    regenerate_profile,
    source_fingerprint,
)
from build_chemistry.projection import normalize_star


NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)


def stored_stars():
    rows = [
        {
            "starred_at": "2025-01-01T10:00:00Z",
            "repo": {
                "id": 10, "node_id": "R_10", "full_name": "tools/one",
                "html_url": "https://github.com/tools/one", "description": "one",
                "language": "Python", "topics": ["local-first", "cli"],
                "owner": {"login": "tools"}, "fork": False, "archived": False,
            },
        },
        {
            "starred_at": "2025-01-02T10:00:00Z",
            "repo": {
                "id": 11, "node_id": "R_11", "full_name": "labs/two",
                "html_url": "https://github.com/labs/two", "description": "two",
                "language": "Rust", "topics": ["cli"],
                "owner": {"login": "labs"}, "fork": True, "archived": False,
            },
        },
    ]
    return tuple(
        normalize_star(row, github_username="octocat", owner_id="owner-1", observed_at=NOW)
        for row in rows
    )


class StoredContext:
    def __init__(self, records):
        self.records = records
        self.read_calls = []

    def read_stars(self, start_time, end_time):
        self.read_calls.append((start_time, end_time))
        return self.records


def test_features_and_fingerprint_are_deterministic_and_order_independent() -> None:
    records = stored_stars()
    features = extract_features(records)
    assert (features.star_count, features.owner_count, features.fork_count) == (2, 2, 1)
    assert [(row.name, row.count) for row in features.topic_counts] == [
        ("cli", 2), ("local-first", 1)
    ]
    assert source_fingerprint(records) == source_fingerprint(reversed(records))


def test_two_lenses_regenerate_distinct_strict_profiles_from_context() -> None:
    context = StoredContext(stored_stars())
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=400)
    default = regenerate_profile(context, start, end, "default")
    chaotic = regenerate_profile(context, start, end, "chaotic-collaborator")
    assert context.read_calls == [(start, end), (start, end)]
    assert default.source_fingerprint == chaotic.source_fingerprint
    assert default.features == chaotic.features
    assert default.record_id != chaotic.record_id
    assert default.personality_name != chaotic.personality_name
    assert default.blurb != chaotic.blurb
    assert BuilderTasteProfile.model_validate_json(default.model_dump_json()) == default


@pytest.mark.parametrize("lens", ["default", "chaotic-collaborator", "pragmatic-builder"])
def test_only_three_versioned_v1_presets_are_available(lens) -> None:
    profile = generate_profile(stored_stars(), lens)
    assert profile.lens == lens
    assert profile.lens_version == "v1"
    payload = profile.model_dump()
    payload["lens"] = "write me a custom prompt"
    with pytest.raises(ValidationError):
        BuilderTasteProfile.model_validate(payload)


def test_profile_schema_rejects_coercion_and_unknown_fields() -> None:
    payload = generate_profile(stored_stars(), "pragmatic-builder").model_dump()
    payload["features"] = {**payload["features"], "star_count": "2"}
    payload["unexpected"] = True
    with pytest.raises(ValidationError):
        BuilderTasteProfile.model_validate(payload)


def test_empty_or_mixed_user_sources_are_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        generate_profile((), "default")
    records = stored_stars()
    other = records[1].model_copy(
        update={"note": records[1].note.model_copy(update={"github_username": "other"})}
    )
    with pytest.raises(ValueError, match="one GitHub username"):
        generate_profile((records[0], other), "default")