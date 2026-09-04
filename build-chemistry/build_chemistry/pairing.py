"""Reciprocal, profile-only Fulcra pairing.

Pair links contain routing metadata, not data or credentials.  Every readiness
check is made against Fulcra so a deleted share takes effect on the next read.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .fulcra_context import (
    ANNOTATION_SOURCE_PREFIX,
    FulcraContextAdapter,
    InvalidStoredRecordError,
    StoredProfileNote,
)
from .models import BuilderTasteProfile
from .comparison import select_compatible_profiles


class InvalidPairLinkError(ValueError):
    """A pair link is malformed or does not route to a profile type."""


class PairNotReadyError(PermissionError):
    """Both current Fulcra shares are required before profiles may be read."""


class PairRoute(BaseModel):
    """Public, non-secret routing metadata carried by a pair link."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    fulcra_user_id: str = Field(min_length=1, max_length=128)
    profile_type: str = Field(pattern=r"^MomentAnnotation/[0-9A-Za-z-]{1,128}$")

    def token(self) -> str:
        payload = self.model_dump_json().encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @classmethod
    def from_token(cls, token: str) -> "PairRoute":
        if not token or len(token) > 2048:
            raise InvalidPairLinkError("invalid pair link")
        try:
            padding = "=" * (-len(token) % 4)
            raw = base64.b64decode(token + padding, altchars=b"-_", validate=True)
            route = cls.model_validate_json(raw)
        except (ValueError, ValidationError) as exc:
            raise InvalidPairLinkError("invalid pair link") from exc
        # Reject alternate/non-canonical encodings so one route has one stable URL.
        if route.token() != token:
            raise InvalidPairLinkError("invalid pair link")
        return route


@dataclass(frozen=True)
class PairStatus:
    state: Literal["waiting-for-you", "waiting-for-them", "ready"]
    outgoing_share_ids: tuple[str, ...]
    incoming: bool


class PairingService:
    """Coordinate direct shares without storing pair state in an app database."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.context = FulcraContextAdapter(client)

    def my_route(self) -> PairRoute:
        types = self.context.ensure_private_types()
        return PairRoute(
            fulcra_user_id=self.client.get_fulcra_userid(),
            profile_type=types.profile,
        )

    def status(self, peer: PairRoute) -> PairStatus:
        """Re-read both directions from Fulcra; no readiness is cached."""
        mine = self.my_route()
        if peer.fulcra_user_id == mine.fulcra_user_id:
            raise InvalidPairLinkError("a pair link must belong to another Fulcra user")

        outgoing_rows = self._bounded_rows(
            self.client.get_datashares(), "outgoing share response"
        )
        incoming_rows = self._bounded_rows(
            self.client.get_shared_datasets(), "incoming share response"
        )
        outgoing_ids = tuple(
            share_id
            for share in outgoing_rows
            if (share_id := self._matching_outgoing_id(share, peer, mine.profile_type))
        )
        incoming = any(
            self._matching_incoming(dataset, peer)
            for dataset in incoming_rows
        )
        if outgoing_ids and incoming:
            state = "ready"
        elif outgoing_ids:
            state = "waiting-for-them"
        else:
            state = "waiting-for-you"
        return PairStatus(state=state, outgoing_share_ids=outgoing_ids, incoming=incoming)

    def share_with(self, peer: PairRoute) -> PairStatus:
        """Explicitly grant the peer access to exactly the local profile type."""
        before = self.status(peer)
        if not before.outgoing_share_ids:
            self.context.create_profile_share(
                name="Build Chemistry profile",
                allowed_user_ids=[peer.fulcra_user_id],
            )
        return self.status(peer)

    def revoke(self, peer: PairRoute) -> PairStatus:
        """Delete every app-safe outgoing profile share for this peer."""
        current = self.status(peer)
        for share_id in current.outgoing_share_ids:
            self.client.delete_datashare(share_id)
        return self.status(peer)

    def read_pair_profiles(
        self, peer: PairRoute, start_time: datetime, end_time: datetime
    ) -> tuple[BuilderTasteProfile, BuilderTasteProfile]:
        """Freshly authorize, then return each user's latest stored profile."""
        status = self.status(peer)
        if status.state != "ready":
            raise PairNotReadyError("both users must share their profile before comparison")
        own = self.context.read_profiles(start_time, end_time)
        remote = self._read_shared_profiles(peer, start_time, end_time)
        return select_compatible_profiles(own, remote)


    @staticmethod
    def _bounded_rows(value: object, label: str) -> list[dict[str, Any]]:
        if (
            not isinstance(value, list)
            or len(value) > 1000
            or any(not isinstance(row, dict) for row in value)
        ):
            raise InvalidPairLinkError(f"{label} is not a bounded object list")
        return value

    @staticmethod
    def _matching_outgoing_id(
        row: object, peer: PairRoute, own_profile_type: str
    ) -> str | None:
        if not isinstance(row, dict):
            return None
        permissions = row.get("permissions")
        exact_recipient = (
            isinstance(permissions, list)
            and len(permissions) == 1
            and isinstance(permissions[0], dict)
            and permissions[0].get("allowed_fulcra_userid") == peer.fulcra_user_id
        )
        share_id = row.get("datashare_id")
        if (
            isinstance(share_id, str)
            and share_id
            and row.get("share_all_data") is False
            and row.get("fulcra_data_types") == [own_profile_type]
            and exact_recipient
        ):
            return share_id
        return None

    @staticmethod
    def _matching_incoming(row: object, peer: PairRoute) -> bool:
        if not isinstance(row, dict) or row.get("fulcra_userid") != peer.fulcra_user_id:
            return False
        permission_id = row.get("permission_id")
        if not isinstance(permission_id, str) or not permission_id:
            return False
        # Missing type metadata cannot prove profile-only consent and therefore
        # must fail closed, just like a broad or mismatched grant.
        if row.get("share_all_data") is not False:
            return False
        data_types = row.get("fulcra_data_types")
        return data_types == [peer.profile_type]

    def _read_shared_profiles(
        self, peer: PairRoute, start_time: datetime, end_time: datetime
    ) -> tuple[BuilderTasteProfile, ...]:
        raw = self.client.fulcra_v1_api_path(
            f"event/{peer.profile_type}",
            {
                "start_time": start_time,
                "end_time": end_time,
                "fulcra_userid": peer.fulcra_user_id,
            },
        )
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list) or len(rows) > 100:
                raise TypeError("shared profile response is not a bounded list")
            source = f"{ANNOTATION_SOURCE_PREFIX}{peer.profile_type.split('/', 1)[1]}"
            profiles = tuple(self._decode_shared_profile(row, source) for row in rows)
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidStoredRecordError(f"invalid shared profile record: {exc}") from exc
        return tuple(sorted(profiles, key=lambda item: (item.generated_at, str(item.record_id))))

    @staticmethod
    def _decode_shared_profile(row: object, annotation_source: str) -> BuilderTasteProfile:
        if not isinstance(row, dict):
            raise TypeError("record is not an object")
        note = row.get("note")
        sources = row.get("sources")
        if not isinstance(note, str) or not isinstance(sources, list) or len(sources) != 3:
            raise TypeError("shared profile has invalid note or sources")
        if sources[-1] != annotation_source:
            raise ValueError("shared record is not routed through the advertised profile type")
        profile = StoredProfileNote.model_validate_json(note).profile
        if row.get("id") != str(profile.record_id):
            raise ValueError("shared profile record ID does not match its payload")
        if datetime.fromisoformat(row["recorded_at"]) != profile.generated_at:
            raise ValueError("shared profile timestamp does not match its payload")
        expected = [
            f"build-chemistry:source:{profile.source_fingerprint}",
            f"build-chemistry:lens:{profile.lens}:{profile.lens_version}",
            annotation_source,
        ]
        if sources != expected:
            raise ValueError("shared profile provenance does not match its payload")
        return profile