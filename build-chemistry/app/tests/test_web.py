import json
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from build_chemistry.fulcra_context import ANNOTATION_SOURCE_PREFIX
from build_chemistry.models import StarProjection
from build_chemistry.projection import normalize_star
from app.web import ATTRIBUTION, create_app


NOW = datetime(2026, 4, 5, 12, tzinfo=timezone.utc)


def projection(*, owner_id: str = "github:octocat", username: str = "octocat") -> StarProjection:
    rows = [
        {
            "starred_at": "2025-02-01T10:00:00Z",
            "repo": {
                "id": 10,
                "node_id": "R_10",
                "full_name": "tools/one",
                "html_url": "https://github.com/tools/one",
                "description": "private-to-the-card repository detail",
                "language": "Python",
                "topics": ["developer-tools", "local-first"],
                "owner": {"login": "tools"},
                "fork": False,
                "archived": False,
            },
        },
        {
            "starred_at": "2025-01-01T10:00:00Z",
            "repo": {
                "id": 11,
                "node_id": "R_11",
                "full_name": "labs/two",
                "html_url": "https://github.com/labs/two",
                "description": None,
                "language": "Python",
                "topics": ["developer-tools"],
                "owner": {"login": "labs"},
                "fork": False,
                "archived": False,
            },
        },
    ]
    records = tuple(
        normalize_star(row, github_username=username, owner_id=owner_id, observed_at=NOW)
        for row in rows
    )
    return StarProjection(github_username=username, records=records)


class FakeFulcra:
    def __init__(self) -> None:
        self.owner_id = "fulcra-owner-1"
        self.annotations: list[dict[str, Any]] = []
        self.tag_rows: list[dict[str, str]] = []
        self.records: dict[str, dict[str, Any]] = {}

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
        return {"upload_id": "test-upload"}


class AccessibilityAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.label_targets: set[str] = set()
        self.control_ids: set[str] = set()
        self.h1_count = 0
        self.has_main = False
        self.has_lang = False
        self.images_without_alt: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag == "html" and values.get("lang"):
            self.has_lang = True
        if tag == "main":
            self.has_main = True
        if tag == "h1":
            self.h1_count += 1
        if tag == "label" and values.get("for"):
            self.label_targets.add(values["for"] or "")
        if tag in {"input", "select", "textarea"} and values.get("type") != "hidden":
            if values.get("id"):
                self.control_ids.add(values["id"] or "")
        if tag == "img" and "alt" not in values:
            self.images_without_alt.append(values.get("src") or "")


def assert_accessible_structure(html: str) -> None:
    audit = AccessibilityAudit()
    audit.feed(html)
    assert audit.has_lang
    assert audit.has_main
    assert audit.h1_count == 1
    assert len(audit.ids) == len(set(audit.ids))
    assert audit.control_ids <= audit.label_targets
    assert audit.images_without_alt == []


def test_no_auth_username_flow_returns_useful_attributed_card() -> None:
    calls: list[dict[str, Any]] = []

    def projector(username: str, **kwargs: Any) -> StarProjection:
        calls.append({"username": username, **kwargs})
        return projection(username=username.lower())

    def forbidden_auth() -> Any:
        raise AssertionError("the public card must not touch Fulcra auth")

    client = TestClient(create_app(projector=projector, fulcra_client_factory=forbidden_auth))
    home = client.get("/")
    response = client.get("/u/Octocat")

    assert home.status_code == 200
    assert response.status_code == 200
    assert calls == [{"username": "Octocat"}]
    assert "Curious Toolkit Builder" in response.text
    assert "Stars sampled" in response.text and ">2<" in response.text
    assert "Top language:" in response.text and "Python" in response.text
    assert ATTRIBUTION in unescape(response.text)
    assert "Authenticate and save privately" in response.text
    assert "private-to-the-card" not in response.text
    assert "github.com/tools/one" not in response.text
    assert_accessible_structure(home.text)
    assert_accessible_structure(response.text)


def test_entry_form_works_without_javascript() -> None:
    client = TestClient(create_app(projector=lambda username: projection(username=username)))
    response = client.get("/card", params={"username": "octocat"})
    assert response.history[0].status_code == 303
    assert response.url.path == "/u/octocat"
    assert response.status_code == 200


def test_authenticated_save_writes_private_stars_and_selected_profile() -> None:
    fulcra = FakeFulcra()
    projector_calls: list[tuple[str, str | None]] = []

    def projector(username: str, *, owner_id: str | None = None) -> StarProjection:
        projector_calls.append((username, owner_id))
        return projection(owner_id=owner_id or "public", username=username)

    client = TestClient(
        create_app(projector=projector, fulcra_client_factory=lambda: fulcra)
    )
    response = client.post(
        "/profiles/save",
        data={"username": "octocat", "lens": "pragmatic-builder"},
    )

    assert response.status_code == 200
    assert projector_calls == [("octocat", fulcra.owner_id)]
    assert "Saved to your Fulcra context" in response.text
    assert "Practical Systems Builder" in response.text
    assert "Try another preset lens" in response.text
    assert len(fulcra.records) == 3
    notes = [json.loads(row["note"]) for row in fulcra.records.values()]
    stars = [note for note in notes if "projection" in note]
    profiles = [note["profile"] for note in notes if "profile" in note]
    assert len(stars) == 2
    assert len(profiles) == 1
    assert profiles[0]["lens"] == "pragmatic-builder"
    assert profiles[0]["lens_version"] == "v1"
    assert all("profile" not in note for note in stars)
    profile_record = next(row for row in fulcra.records.values() if "profile" in json.loads(row["note"]))
    assert profile_record["sources"][-1].startswith(ANNOTATION_SOURCE_PREFIX)
    assert_accessible_structure(response.text)


def test_failed_auth_never_projects_or_claims_a_save() -> None:
    projected = False

    def projector(_username: str, **_kwargs: Any) -> StarProjection:
        nonlocal projected
        projected = True
        return projection()

    def unauthenticated() -> Any:
        raise RuntimeError("secret SDK detail")

    client = TestClient(create_app(projector=projector, fulcra_client_factory=unauthenticated))
    response = client.post(
        "/profiles/save", data={"username": "octocat", "lens": "default"}
    )

    assert response.status_code == 401
    assert not projected
    assert "Nothing was saved" in response.text
    assert "secret SDK detail" not in response.text
    assert_accessible_structure(response.text)


def test_unknown_lens_is_rejected_before_authentication() -> None:
    def forbidden_auth() -> Any:
        raise AssertionError("invalid lens must fail first")

    client = TestClient(create_app(fulcra_client_factory=forbidden_auth))
    response = client.post(
        "/profiles/save", data={"username": "octocat", "lens": "invented prompt"}
    )
    assert response.status_code == 400
    assert "Choose one of the available" in response.text


@pytest.mark.live_github
@pytest.mark.live_fulcra
def test_live_web_save_creates_real_fulcra_records() -> None:
    """Bounded acceptance: one real public star and one real derived profile."""
    from app.fulcra_client import FulcraAuthError, get_fulcra_client
    from build_chemistry import FulcraContextAdapter, generate_profile, project_public_stars

    try:
        fulcra = get_fulcra_client()
    except FulcraAuthError as exc:
        pytest.skip(str(exc))

    saved_projection: StarProjection | None = None

    def bounded_projector(
        username: str, *, owner_id: str | None = None
    ) -> StarProjection:
        nonlocal saved_projection
        saved_projection = project_public_stars(username, owner_id=owner_id, limit=1)
        return saved_projection

    client = TestClient(
        create_app(projector=bounded_projector, fulcra_client_factory=lambda: fulcra)
    )
    response = client.post(
        "/profiles/save", data={"username": "octocat", "lens": "default"}
    )
    assert response.status_code == 200
    assert "Saved to your Fulcra context" in response.text
    assert saved_projection is not None

    expected_star = saved_projection.records[0]
    expected_profile = generate_profile(saved_projection.records, "default")
    adapter = FulcraContextAdapter(fulcra)
    found_star_ids: set[str] = set()
    found_profile_ids: set[str] = set()
    for _ in range(20):
        found_star_ids = {
            str(record.record_id)
            for record in adapter.read_stars(
                expected_star.recorded_at - timedelta(microseconds=1),
                expected_star.recorded_at + timedelta(microseconds=1),
            )
        }
        found_profile_ids = {
            str(profile.record_id)
            for profile in adapter.read_profiles(
                expected_profile.generated_at - timedelta(microseconds=1),
                expected_profile.generated_at + timedelta(microseconds=1),
            )
        }
        if str(expected_star.record_id) in found_star_ids and str(expected_profile.record_id) in found_profile_ids:
            break
        time.sleep(1)

    assert str(expected_star.record_id) in found_star_ids
    assert str(expected_profile.record_id) in found_profile_ids
