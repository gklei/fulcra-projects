from datetime import datetime, timedelta, timezone
from html import unescape
import os
from pathlib import Path
import time
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.web import ATTRIBUTION, create_app
from build_chemistry.comparison import (
    IncompatibleProfilesError,
    MissingProfileError,
    generate_comparison,
    select_compatible_profiles,
)
from build_chemistry.models import (
    BuilderTasteFeatures,
    BuilderTasteProfile,
    FeatureCount,
    LensName,
)
from build_chemistry.pairing import PairNotReadyError, PairingService

from test_pairing import PairFakeFulcra, bob_route, grant_from_bob
from test_web import assert_accessible_structure


BASE_TIME = datetime(2026, 6, 1, tzinfo=timezone.utc)


def profile(
    username: str,
    *,
    lens: LensName = "default",
    instant: datetime = BASE_TIME,
    identity: int = 1,
    language: str = "Python",
    topic: str = "developer-tools",
) -> BuilderTasteProfile:
    return BuilderTasteProfile(
        record_id=UUID(int=identity),
        generated_at=instant,
        github_username=username,
        lens=lens,
        source_fingerprint=f"sha256:{identity:064x}",
        features=BuilderTasteFeatures(
            star_count=4,
            owner_count=3,
            fork_count=identity % 2,
            archived_count=0,
            language_counts=(FeatureCount(name=language, count=3),),
            topic_counts=(FeatureCount(name=topic, count=2),),
        ),
        personality_name=(
            "Curious Toolkit Builder"
            if lens == "default"
            else "Practical Systems Builder"
        ),
        blurb=f"@{username} collects useful {language} tools around {topic}.",
    )


def test_selects_newest_symmetric_compatible_contract() -> None:
    old = BASE_TIME - timedelta(days=4)
    recent = BASE_TIME - timedelta(days=1)
    first_default_old = profile("alice", instant=old, identity=1)
    first_default_new = profile("alice", instant=recent, identity=2)
    first_pragmatic = profile(
        "alice", lens="pragmatic-builder", instant=BASE_TIME, identity=3
    )
    second_default = profile("bob", instant=BASE_TIME, identity=4)
    second_pragmatic_old = profile(
        "bob", lens="pragmatic-builder", instant=old, identity=5
    )

    selected = select_compatible_profiles(
        (first_default_old, first_default_new, first_pragmatic),
        (second_default, second_pragmatic_old),
    )
    assert selected == (first_default_new, second_default)
    assert select_compatible_profiles(
        (second_default, second_pragmatic_old),
        (first_default_old, first_default_new, first_pragmatic),
    ) == (second_default, first_default_new)


def test_missing_and_incompatible_profiles_fail_clearly() -> None:
    with pytest.raises(MissingProfileError, match="Neither"):
        select_compatible_profiles((), ())
    with pytest.raises(MissingProfileError, match="other builder"):
        select_compatible_profiles((profile("alice"),), ())
    with pytest.raises(IncompatibleProfilesError, match="same preset lens"):
        select_compatible_profiles(
            (profile("alice", lens="default", identity=1),),
            (profile("bob", lens="pragmatic-builder", identity=2),),
        )


def test_comparison_has_five_fields_and_is_order_independent() -> None:
    alice = profile("alice", identity=10, language="Python", topic="local-first")
    bob = profile("bob", identity=11, language="Rust", topic="local-first")

    result = generate_comparison(alice, bob)
    assert generate_comparison(bob, alice) == result
    assert [item.github_username for item in result.personalities] == ["alice", "bob"]
    assert "local-first" in result.shared_obsession
    assert "Python" in result.productive_disagreement
    assert "Rust" in result.build_idea
    assert "score" not in result.chemistry_verdict.lower()
    assert set(result.model_dump()) == {
        "schema_version",
        "personalities",
        "shared_obsession",
        "productive_disagreement",
        "build_idea",
        "chemistry_verdict",
        "lens",
        "lens_version",
    }


def test_ready_pair_renders_polished_attributed_exportable_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fulcra = PairFakeFulcra()
    PairingService(fulcra).share_with(bob_route())
    grant_from_bob(fulcra)
    alice = profile("alice", identity=20, topic="local-first")
    bob = profile("bob", identity=21, language="Rust", topic="local-first")
    monkeypatch.setattr(
        PairingService,
        "read_pair_profiles",
        lambda self, peer, start, end: (alice, bob),
    )
    web = TestClient(create_app(fulcra_client_factory=lambda: fulcra))
    token = bob_route().token()

    pair_page = web.get(f"/pair/{token}")
    response = web.get(f"/pair/{token}/compare")

    assert "Reveal our Build Chemistry" in pair_page.text
    assert response.status_code == 200
    for label in (
        "Curious Toolkit Builder",
        "Shared obsession",
        "Productive disagreement",
        "Build this together",
        "Chemistry verdict",
    ):
        assert label in response.text
    assert ATTRIBUTION in unescape(response.text)
    assert "saved or copied cards cannot be remotely revoked" in response.text
    assert "checks both Fulcra shares again on every visit" in response.text
    assert "Print or save card" in response.text
    assert_accessible_structure(response.text)


def test_compare_route_reports_not_ready_missing_and_incompatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fulcra = PairFakeFulcra()
    web = TestClient(create_app(fulcra_client_factory=lambda: fulcra))
    token = bob_route().token()

    blocked = web.get(f"/pair/{token}/compare")
    assert blocked.status_code == 409
    assert "Comparison not ready" in blocked.text
    assert "No comparison was generated" not in blocked.text

    def missing(self, peer, start, end):
        raise MissingProfileError("The other builder needs a saved profile before comparing.")

    monkeypatch.setattr(PairingService, "read_pair_profiles", missing)
    missing_response = web.get(f"/pair/{token}/compare")
    assert missing_response.status_code == 409
    assert "Saved profile missing" in missing_response.text
    assert "other builder needs a saved profile" in missing_response.text

    monkeypatch.setattr(
        PairingService,
        "read_pair_profiles",
        lambda self, peer, start, end: (
            profile("alice", lens="default", identity=30),
            profile("bob", lens="pragmatic-builder", identity=31),
        ),
    )
    incompatible = web.get(f"/pair/{token}/compare")
    assert incompatible.status_code == 409
    assert "Profiles do not match" in incompatible.text
    assert "same preset lens and version" in incompatible.text


@pytest.mark.live_github
@pytest.mark.live_fulcra
def test_live_two_shared_profiles_produce_a_valid_reveal() -> None:
    """Bounded acceptance check using two real Fulcra users and public stars."""
    from app.fulcra_client import FulcraAuthError, get_fulcra_client
    from build_chemistry.lenses import generate_profile
    from build_chemistry.projection import project_public_stars

    peer_credentials = os.environ.get("FULCRA_PAIR_PEER_CREDENTIALS_PATH")
    if not peer_credentials or not Path(peer_credentials).is_file():
        pytest.skip("set FULCRA_PAIR_PEER_CREDENTIALS_PATH for the two-user reveal check")
    try:
        alice_client = get_fulcra_client()
        bob_client = get_fulcra_client(peer_credentials)
    except FulcraAuthError as exc:
        pytest.skip(str(exc))

    alice = PairingService(alice_client)
    bob = PairingService(bob_client)
    alice_route, bob_route_value = alice.my_route(), bob.my_route()
    if alice_route.fulcra_user_id == bob_route_value.fulcra_user_id:
        pytest.skip("two distinct real Fulcra users are required")

    # Do not alter unrelated or pre-existing sharing relationships.
    if any(
        permission.get("allowed_fulcra_userid") == bob_route_value.fulcra_user_id
        for share in alice_client.get_datashares()
        for permission in share.get("permissions", [])
    ) or any(
        permission.get("allowed_fulcra_userid") == alice_route.fulcra_user_id
        for share in bob_client.get_datashares()
        for permission in share.get("permissions", [])
    ):
        pytest.skip("acceptance check requires users with no pre-existing outgoing shares")

    alice_projection = project_public_stars(
        "octocat", owner_id=alice_route.fulcra_user_id, limit=5
    )
    bob_projection = project_public_stars(
        "torvalds", owner_id=bob_route_value.fulcra_user_id, limit=5
    )
    expected_alice = generate_profile(alice_projection.records, "default")
    expected_bob = generate_profile(bob_projection.records, "default")
    alice.context.write_stars(alice_projection)
    alice.context.write_profile(expected_alice)
    bob.context.write_stars(bob_projection)
    bob.context.write_profile(expected_bob)

    alice_created: set[str] = set()
    bob_created: set[str] = set()
    try:
        alice.share_with(bob_route_value)
        alice_created = set(alice.status(bob_route_value).outgoing_share_ids)
        bob.share_with(alice_route)
        bob_created = set(bob.status(alice_route).outgoing_share_ids)
        assert alice_created and bob_created

        selected = None
        expected_ids = {expected_alice.record_id, expected_bob.record_id}
        for _ in range(20):
            try:
                candidate = alice.read_pair_profiles(
                    bob_route_value,
                    datetime(2020, 1, 1, tzinfo=timezone.utc),
                    datetime(2100, 1, 1, tzinfo=timezone.utc),
                )
            except (MissingProfileError, IncompatibleProfilesError, PairNotReadyError):
                time.sleep(1)
                continue
            if {item.record_id for item in candidate} == expected_ids:
                selected = candidate
                break
            time.sleep(1)
        assert selected is not None

        result = generate_comparison(*selected)
        assert len(result.personalities) == 2
        assert result.shared_obsession
        assert result.productive_disagreement
        assert result.build_idea
        assert result.chemistry_verdict
    finally:
        for share_id in alice_created:
            alice_client.delete_datashare(share_id)
        for share_id in bob_created:
            bob_client.delete_datashare(share_id)
