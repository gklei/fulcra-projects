from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from app.web import create_app
from build_chemistry.comparison import MissingProfileError, select_compatible_profiles
from build_chemistry.fulcra_context import PROFILE_TYPE_NAME, STAR_TYPE_NAME
from build_chemistry.pairing import (
    InvalidPairLinkError,
    PairNotReadyError,
    PairRoute,
    PairingService,
)


class PairFakeFulcra:
    def __init__(self) -> None:
        self.owner_id = "alice"
        self.outgoing: list[dict[str, Any]] = []
        self.incoming: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.data_paths: list[str] = []

    def get_fulcra_userid(self) -> str:
        return self.owner_id

    def annotations_catalog(self, fulcra_userid: str | None = None) -> list[dict[str, Any]]:
        owner = fulcra_userid or self.owner_id
        return [
            {
                "id": f"star-{owner}",
                "name": STAR_TYPE_NAME,
                "annotation_type": "moment",
                "deleted_at": None,
            },
            {
                "id": f"profile-{owner}",
                "name": PROFILE_TYPE_NAME,
                "annotation_type": "moment",
                "deleted_at": None,
            },
        ]

    def get_datashares(self) -> list[dict[str, Any]]:
        return list(self.outgoing)

    def get_shared_datasets(self) -> list[dict[str, Any]]:
        return list(self.incoming)

    def create_datashare(self, **kwargs: Any) -> dict[str, Any]:
        row = {
            "datashare_id": f"share-{len(self.outgoing) + 1}",
            "datashare_name": kwargs["datashare_name"],
            "fulcra_data_types": kwargs["fulcra_data_types"],
            "permissions": [
                {"allowed_fulcra_userid": user_id}
                for user_id in kwargs["allowed_user_ids"]
            ],
            "share_all_data": kwargs["share_all_data"],
        }
        self.outgoing.append(row)
        return row

    def delete_datashare(self, datashare_id: str) -> None:
        self.deleted.append(datashare_id)
        self.outgoing = [row for row in self.outgoing if row["datashare_id"] != datashare_id]

    def fulcra_v1_api_path(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        self.data_paths.append(path)
        return b"[]"


def bob_route() -> PairRoute:
    return PairRoute(fulcra_user_id="bob", profile_type="MomentAnnotation/profile-bob")


def grant_from_bob(client: PairFakeFulcra) -> None:
    client.incoming = [
        {
            "permission_id": "permission-bob",
            "fulcra_userid": "bob",
            "fulcra_data_types": ["MomentAnnotation/profile-bob"],
            "share_all_data": False,
        }
    ]


def test_pair_link_round_trip_and_rejects_tampering() -> None:
    route = bob_route()
    assert PairRoute.from_token(route.token()) == route
    with pytest.raises(InvalidPairLinkError):
        PairRoute.from_token(route.token() + "!")
    with pytest.raises(InvalidPairLinkError):
        PairRoute.from_token("")


def test_pair_waits_for_both_explicit_profile_only_shares() -> None:
    client = PairFakeFulcra()
    service = PairingService(client)

    assert service.status(bob_route()).state == "waiting-for-you"
    after_share = service.share_with(bob_route())
    assert after_share.state == "waiting-for-them"
    assert client.outgoing[0]["fulcra_data_types"] == [
        "MomentAnnotation/profile-alice"
    ]
    assert client.outgoing[0]["share_all_data"] is False
    assert "MomentAnnotation/star-alice" not in client.outgoing[0]["fulcra_data_types"]

    grant_from_bob(client)
    assert service.status(bob_route()).state == "ready"
    # Repeating consent is idempotent rather than creating broader/duplicate shares.
    service.share_with(bob_route())
    assert len(client.outgoing) == 1


def test_broad_or_wrong_type_shares_do_not_count_as_consent() -> None:
    client = PairFakeFulcra()
    service = PairingService(client)
    client.outgoing = [
        {
            "datashare_id": "unsafe",
            "fulcra_data_types": ["MomentAnnotation/star-alice"],
            "permissions": [{"allowed_fulcra_userid": "bob"}],
            "share_all_data": False,
        }
    ]
    client.incoming = [
        {
            "permission_id": "broad",
            "fulcra_userid": "bob",
            "share_all_data": True,
        }
    ]
    status = service.status(bob_route())
    assert status.state == "waiting-for-you"
    assert status.incoming is False


def test_missing_type_metadata_and_multi_recipient_shares_fail_closed() -> None:
    client = PairFakeFulcra()
    service = PairingService(client)
    client.outgoing = [
        {
            "datashare_id": "shared-with-several-people",
            "fulcra_data_types": ["MomentAnnotation/profile-alice"],
            "permissions": [
                {"allowed_fulcra_userid": "bob"},
                {"allowed_fulcra_userid": "charlie"},
            ],
            "share_all_data": False,
        }
    ]
    client.incoming = [{"permission_id": "unscoped", "fulcra_userid": "bob"}]

    status = service.status(bob_route())
    assert status.state == "waiting-for-you"
    assert status.outgoing_share_ids == ()
    assert status.incoming is False
    service.revoke(bob_route())
    assert client.deleted == []

    client.outgoing[0]["permissions"] = [
        {"allowed_fulcra_userid": "bob"},
        {"allowed_fulcra_userid": "bob"},
    ]
    assert service.status(bob_route()).outgoing_share_ids == ()


def test_revocation_blocks_a_fresh_read_without_touching_star_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    client = PairFakeFulcra()
    service = PairingService(client)
    service.share_with(bob_route())
    grant_from_bob(client)

    sentinel = object()
    monkeypatch.setattr(service.context, "read_profiles", lambda start, end: (sentinel,))
    monkeypatch.setattr(service, "_read_shared_profiles", lambda peer, start, end: (sentinel,))
    monkeypatch.setattr(
        "build_chemistry.pairing.select_compatible_profiles",
        lambda own, remote: (sentinel, sentinel),
    )
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert service.read_pair_profiles(bob_route(), start, end) == (sentinel, sentinel)

    status = service.revoke(bob_route())
    assert status.state == "waiting-for-you"
    assert client.deleted == ["share-1"]
    with pytest.raises(PairNotReadyError, match="both users"):
        service.read_pair_profiles(bob_route(), start, end)
    assert all("star" not in path.lower() for path in client.data_paths)


def test_incoming_consent_must_match_the_type_route_exactly() -> None:
    client = PairFakeFulcra()
    service = PairingService(client)
    client.incoming = [{
        "permission_id": "from-bob",
        "fulcra_userid": "bob",
        "fulcra_data_types": ["MomentAnnotation/star-bob"],
        "share_all_data": False,
    }]
    assert service.status(bob_route()).incoming is False


def test_ready_check_uses_share_metadata_without_requesting_peer_catalog() -> None:
    client = PairFakeFulcra()
    service = PairingService(client)
    service.share_with(bob_route())
    grant_from_bob(client)

    assert service.status(bob_route()).state == "ready"
    # Fulcra permits a recipient to read the shared subtype but does not permit
    # inspecting the owner's annotation catalog. The exact incoming subtype
    # grant is therefore the routing authority; records are schema-validated
    # before the comparison layer receives them.
    assert client.data_paths == []


def test_pair_web_flow_exposes_waiting_ready_and_revocation_states() -> None:
    fulcra = PairFakeFulcra()
    web = TestClient(create_app(fulcra_client_factory=lambda: fulcra))
    token = bob_route().token()

    invite = web.get("/pair")
    assert invite.status_code == 200
    assert "Pair links contain only your Fulcra user ID and profile-type route" in invite.text

    waiting = web.get(f"/pair/{token}")
    assert waiting.status_code == 200
    assert "Your approval is needed" in waiting.text
    assert "Share only my profile" in waiting.text

    shared = web.post(f"/pair/{token}/share", follow_redirects=True)
    assert shared.status_code == 200
    assert "Waiting for the other builder" in shared.text

    grant_from_bob(fulcra)
    ready = web.get(f"/pair/{token}")
    assert "Ready to compare" in ready.text
    assert "Revoke my profile share" in ready.text

    revoked = web.post(f"/pair/{token}/revoke", follow_redirects=True)
    assert revoked.status_code == 200
    assert "Your approval is needed" in revoked.text


@pytest.mark.live_fulcra
def test_live_two_user_reciprocal_consent_and_revocation() -> None:
    """Bounded acceptance check requiring an explicitly supplied second user."""
    from app.fulcra_client import FulcraAuthError, get_fulcra_client

    peer_credentials = os.environ.get("FULCRA_PAIR_PEER_CREDENTIALS_PATH")
    if not peer_credentials:
        pytest.skip("set FULCRA_PAIR_PEER_CREDENTIALS_PATH for the two-user check")
    if not Path(peer_credentials).is_file():
        pytest.skip("peer Fulcra credentials file is unavailable")
    try:
        alice_client = get_fulcra_client()
        bob_client = get_fulcra_client(peer_credentials)
    except FulcraAuthError as exc:
        pytest.skip(str(exc))

    alice = PairingService(alice_client)
    bob = PairingService(bob_client)
    alice_route, bob_route_value = alice.my_route(), bob.my_route()
    alice_types = alice.context.ensure_private_types()
    bob_types = bob.context.ensure_private_types()
    if alice_route.fulcra_user_id == bob_route_value.fulcra_user_id:
        pytest.skip("two distinct real Fulcra users are required")
    existing_between_users = (
        any(
            permission.get("allowed_fulcra_userid") == bob_route_value.fulcra_user_id
            for share in alice_client.get_datashares()
            for permission in share.get("permissions", [])
        )
        or any(
            permission.get("allowed_fulcra_userid") == alice_route.fulcra_user_id
            for share in bob_client.get_datashares()
            for permission in share.get("permissions", [])
        )
        or any(
            row.get("fulcra_userid") == bob_route_value.fulcra_user_id
            for row in alice_client.get_shared_datasets()
        )
        or any(
            row.get("fulcra_userid") == alice_route.fulcra_user_id
            for row in bob_client.get_shared_datasets()
        )
    )
    if existing_between_users:
        pytest.skip("acceptance check requires users with no pre-existing shares")

    alice_before = {
        str(share["datashare_id"])
        for share in alice_client.get_datashares()
        if share.get("datashare_id")
    }
    bob_before = {
        str(share["datashare_id"])
        for share in bob_client.get_datashares()
        if share.get("datashare_id")
    }
    alice_created: set[str] = set()
    bob_created: set[str] = set()
    try:
        alice.share_with(bob_route_value)
        alice_created = set(alice.status(bob_route_value).outgoing_share_ids)
        bob.share_with(alice_route)
        bob_created = set(bob.status(alice_route).outgoing_share_ids)
        assert alice_created and bob_created

        for _ in range(15):
            if alice.status(bob_route_value).state == "ready" and bob.status(alice_route).state == "ready":
                break
            time.sleep(1)
        assert alice.status(bob_route_value).state == "ready"
        assert bob.status(alice_route).state == "ready"

        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2030, 1, 1, tzinfo=timezone.utc)
        alice_own = alice.context.read_profiles(start, end)
        bob_own = bob.context.read_profiles(start, end)
        # Real shared-subtype reads return only the owner's profile records.
        # An empty profile type remains a valid grant, but comparison stays
        # blocked until its owner saves a profile.
        assert alice._read_shared_profiles(bob_route_value, start, end) == bob_own
        assert bob._read_shared_profiles(alice_route, start, end) == alice_own
        if alice_own and bob_own:
            expected = select_compatible_profiles(alice_own, bob_own)
            assert alice.read_pair_profiles(bob_route_value, start, end) == expected
            assert bob.read_pair_profiles(alice_route, start, end) == (
                expected[1],
                expected[0],
            )
        else:
            with pytest.raises(MissingProfileError, match="saved profile"):
                alice.read_pair_profiles(bob_route_value, start, end)

        # Reciprocal profile grants must not make either private star subtype
        # readable by the other real account.
        with pytest.raises(HTTPError):
            alice_client.fulcra_v1_api_path(
                f"event/{bob_types.star}",
                {
                    "start_time": "2020-01-01T00:00:00+00:00",
                    "end_time": "2030-01-01T00:00:00+00:00",
                    "fulcra_userid": bob_route_value.fulcra_user_id,
                },
            )
        with pytest.raises(HTTPError):
            bob_client.fulcra_v1_api_path(
                f"event/{alice_types.star}",
                {
                    "start_time": "2020-01-01T00:00:00+00:00",
                    "end_time": "2030-01-01T00:00:00+00:00",
                    "fulcra_userid": alice_route.fulcra_user_id,
                },
            )

        # Delete one direction and prove the other user's next authorization
        # check blocks before requesting either profile or star records.
        for share_id in alice_created:
            alice_client.delete_datashare(share_id)
        alice_created.clear()
        for _ in range(15):
            if bob.status(alice_route).state != "ready":
                break
            time.sleep(1)
        with pytest.raises(PairNotReadyError):
            bob.read_pair_profiles(
                alice_route,
                datetime(2020, 1, 1, tzinfo=timezone.utc),
                datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
    finally:
        # Discover shares again instead of relying only on IDs recorded after
        # create_datashare returned. A response can be lost after Fulcra has
        # created the share, and that failure must not leak test grants.
        alice_created.update(
            str(share["datashare_id"])
            for share in alice_client.get_datashares()
            if share.get("datashare_id")
            and str(share["datashare_id"]) not in alice_before
        )
        bob_created.update(
            str(share["datashare_id"])
            for share in bob_client.get_datashares()
            if share.get("datashare_id")
            and str(share["datashare_id"]) not in bob_before
        )
        for share_id in alice_created:
            alice_client.delete_datashare(share_id)
        for share_id in bob_created:
            bob_client.delete_datashare(share_id)