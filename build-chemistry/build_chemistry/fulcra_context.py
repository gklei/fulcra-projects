"""Private Fulcra Context storage for star projections and derived profiles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Protocol, Sequence
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import BuilderTasteProfile, GitHubStarredRepository, ProjectionNote, StarProjection

API_VERSION = "v1alpha1"
BASE_TYPE = "MomentAnnotation"
STAR_TYPE_NAME = "GitHubStarredRepository"
PROFILE_TYPE_NAME = "BuilderTasteProfile"
STAR_TYPE_TAGS = ("build-chemistry", "build-chemistry-private-stars")
PROFILE_TYPE_TAGS = ("build-chemistry", "build-chemistry-profile")
ANNOTATION_SOURCE_PREFIX = "com.fulcradynamics.annotation."
MAX_FULCRA_TAG_LENGTH = 30


class StoredStarNote(BaseModel):
    """Strict, versioned envelope for values Fulcra stores as note text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    projection: ProjectionNote
    tags: tuple[str, ...] = Field(min_length=4)


class StoredProfileNote(BaseModel):
    """Strict envelope for a share-safe derived profile."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    profile: BuilderTasteProfile


class ContextConfigurationError(RuntimeError):
    """The user's Fulcra Context has ambiguous or malformed application types."""


class UnsafeShareError(ValueError):
    """A requested share could expose private source records."""


class InvalidStoredRecordError(ValueError):
    """A stored Context record does not satisfy the projection schema."""


class FulcraClient(Protocol):
    def get_fulcra_userid(self) -> str: ...
    def annotations_catalog(self, fulcra_userid: str | None = None) -> list[dict[str, Any]]: ...
    def create_annotation(self, **kwargs: Any) -> dict[str, Any]: ...
    def create_tags(self, tag_names: list[str]) -> list[dict[str, str]]: ...
    def tags(self) -> list[dict[str, str]]: ...
    def record_data_type(
        self, data_type: str, records: list[dict[str, Any]], api_version: str
    ) -> dict[str, Any]: ...
    def fulcra_v1_api_path(
        self, path: str, params: dict[str, Any] | None = None
    ) -> bytes: ...
    def create_datashare(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_datashares(self) -> list[dict[str, Any]]: ...
    def get_shared_datasets(self) -> list[dict[str, Any]]: ...
    def delete_datashare(self, datashare_id: str) -> None: ...


@dataclass(frozen=True)
class ContextTypes:
    """Owner-specific custom types used by Build Chemistry."""

    star: str
    profile: str


class FulcraContextAdapter:
    """Small SDK adapter that keeps stars private and operations bounded."""

    def __init__(self, client: FulcraClient) -> None:
        self._client = client
        self._types: ContextTypes | None = None

    def ensure_private_types(self) -> ContextTypes:
        """Discover or create the two owner-local MomentAnnotation subtypes."""
        owner_id = self._client.get_fulcra_userid()
        annotations = self._client.annotations_catalog(fulcra_userid=owner_id)
        star = self._find_active_type(annotations, STAR_TYPE_NAME)
        profile = self._find_active_type(annotations, PROFILE_TYPE_NAME)

        if star is None:
            star = self._create_type(
                STAR_TYPE_NAME,
                "Private normalized GitHub star events for Build Chemistry.",
                STAR_TYPE_TAGS,
            )
        if profile is None:
            profile = self._create_type(
                PROFILE_TYPE_NAME,
                "Derived Builder Taste Profiles that may be shared directly.",
                PROFILE_TYPE_TAGS,
            )
        self._types = ContextTypes(star=star, profile=profile)
        return self._types

    @staticmethod
    def _find_active_type(annotations: Iterable[dict[str, Any]], name: str) -> str | None:
        matches = [
            row
            for row in annotations
            if row.get("name") == name
            and row.get("annotation_type") == "moment"
            and row.get("deleted_at") is None
        ]
        if len(matches) > 1:
            raise ContextConfigurationError(f"multiple active Fulcra types named {name!r}")
        if not matches:
            return None
        annotation_id = matches[0].get("id")
        if not isinstance(annotation_id, str) or not annotation_id:
            raise ContextConfigurationError(f"Fulcra type {name!r} has no valid ID")
        return f"{BASE_TYPE}/{annotation_id.lower()}"

    def _create_type(self, name: str, description: str, tags: tuple[str, ...]) -> str:
        created = self._client.create_annotation(
            annotation_type="moment",
            name=name,
            description=description,
            tags=list(tags),
        )
        annotation_id = created.get("id")
        if not isinstance(annotation_id, str) or not annotation_id:
            raise ContextConfigurationError(f"Fulcra did not return an ID for {name!r}")
        return f"{BASE_TYPE}/{annotation_id.lower()}"

    def write_stars(self, projection: StarProjection) -> dict[str, Any]:
        """Write a bounded projection using deterministic record IDs.

        Fulcra ingestion treats the supplied ``id`` as the record identity, so
        replaying the same projection cannot create duplicate events.
        """
        if len(projection.records) > 100:
            raise ValueError("a star write cannot exceed 100 records")
        types = self._types or self.ensure_private_types()
        type_uuid = types.star.split("/", 1)[1]
        tag_names = _ordered_unique(
            _fulcra_tag(tag) for record in projection.records for tag in record.tags
        )
        tag_rows = self._client.create_tags(tag_names)
        tag_ids = {row["name"]: row["id"] for row in tag_rows}
        records = [
            {
                "id": str(record.record_id),
                "recorded_at": record.recorded_at.isoformat(),
                "note": json.dumps(
                    StoredStarNote(projection=record.note, tags=record.tags).model_dump(
                        mode="json"
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "tags": [tag_ids[_fulcra_tag(tag)] for tag in record.tags],
                "sources": [*record.sources, f"{ANNOTATION_SOURCE_PREFIX}{type_uuid}"],
            }
            for record in projection.records
        ]
        return self._client.record_data_type(BASE_TYPE, records, API_VERSION)

    def read_stars(
        self, start_time: datetime, end_time: datetime
    ) -> tuple[GitHubStarredRepository, ...]:
        """Read only star-subtype records in the half-open requested interval."""
        _validate_window(start_time, end_time)
        types = self._types or self.ensure_private_types()
        raw = self._client.fulcra_v1_api_path(
            f"event/{types.star}",
            {"start_time": start_time, "end_time": end_time},
        )
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("response is not a list")
            tag_names = {row["id"]: row["name"] for row in self._client.tags()}
            annotation_source = f"{ANNOTATION_SOURCE_PREFIX}{types.star.split('/', 1)[1]}"
            records = tuple(
                self._decode_star(row, tag_names, annotation_source) for row in rows
            )
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidStoredRecordError(f"invalid Fulcra star record: {exc}") from exc
        return tuple(sorted(records, key=lambda record: (record.recorded_at, str(record.record_id))))

    def write_profile(self, profile: BuilderTasteProfile) -> dict[str, Any]:
        """Persist a derived profile idempotently in its share-safe subtype."""
        types = self._types or self.ensure_private_types()
        type_uuid = types.profile.split("/", 1)[1]
        logical_tags = (
            "build-chemistry-profile",
            "builder-profile-v1",
            f"lens:{profile.lens}",
            f"github-user:{profile.github_username}",
        )
        tag_names = [_fulcra_tag(tag) for tag in logical_tags]
        tag_rows = self._client.create_tags(tag_names)
        tag_ids = {row["name"]: row["id"] for row in tag_rows}
        record = {
            "id": str(profile.record_id),
            "recorded_at": profile.generated_at.isoformat(),
            "note": json.dumps(
                StoredProfileNote(profile=profile).model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            "tags": [tag_ids[tag] for tag in tag_names],
            "sources": [
                f"build-chemistry:source:{profile.source_fingerprint}",
                f"build-chemistry:lens:{profile.lens}:{profile.lens_version}",
                f"{ANNOTATION_SOURCE_PREFIX}{type_uuid}",
            ],
        }
        return self._client.record_data_type(BASE_TYPE, [record], API_VERSION)

    def read_profiles(
        self, start_time: datetime, end_time: datetime
    ) -> tuple[BuilderTasteProfile, ...]:
        """Read and strictly validate only profile-subtype records."""
        _validate_window(start_time, end_time)
        types = self._types or self.ensure_private_types()
        raw = self._client.fulcra_v1_api_path(
            f"event/{types.profile}",
            {"start_time": start_time, "end_time": end_time},
        )
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("response is not a list")
            tag_names = {row["id"]: row["name"] for row in self._client.tags()}
            annotation_source = f"{ANNOTATION_SOURCE_PREFIX}{types.profile.split('/', 1)[1]}"
            profiles = tuple(
                self._decode_profile(row, tag_names, annotation_source) for row in rows
            )
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
            raise InvalidStoredRecordError(f"invalid Fulcra profile record: {exc}") from exc
        return tuple(
            sorted(profiles, key=lambda profile: (profile.generated_at, str(profile.record_id)))
        )

    @staticmethod
    def _decode_profile(
        row: dict[str, Any], tag_names: dict[str, str], annotation_source: str
    ) -> BuilderTasteProfile:
        if not isinstance(row, dict):
            raise TypeError("record is not an object")
        raw_note = row.get("note")
        if not isinstance(raw_note, str):
            raise TypeError("note is not a JSON string")
        raw_tags = row.get("tags")
        if not isinstance(raw_tags, list):
            raise TypeError("profile tags are not a list")
        sources = row.get("sources")
        if not isinstance(sources, list) or len(sources) != 3:
            raise TypeError("profile sources are not an ordered list")
        if sources[-1] != annotation_source:
            raise ValueError("record is missing its profile type source")
        envelope = StoredProfileNote.model_validate_json(raw_note)
        profile = envelope.profile
        if row.get("id") != str(profile.record_id):
            raise ValueError("profile record ID does not match its payload")
        try:
            recorded_at = datetime.fromisoformat(row["recorded_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("profile has invalid recorded_at") from exc
        if recorded_at != profile.generated_at:
            raise ValueError("profile recorded_at does not match generated_at")
        expected_tags = tuple(
            _fulcra_tag(tag)
            for tag in (
                "build-chemistry-profile",
                "builder-profile-v1",
                f"lens:{profile.lens}",
                f"github-user:{profile.github_username}",
            )
        )
        if tuple(tag_names[tag_id] for tag_id in raw_tags) != expected_tags:
            raise ValueError("profile tags do not match its payload")
        expected_sources = [
            f"build-chemistry:source:{profile.source_fingerprint}",
            f"build-chemistry:lens:{profile.lens}:{profile.lens_version}",
            annotation_source,
        ]
        if sources != expected_sources:
            raise ValueError("profile sources do not match its provenance")
        return profile

    @staticmethod
    def _decode_star(
        row: dict[str, Any], tag_names: dict[str, str], annotation_source: str
    ) -> GitHubStarredRepository:
        if not isinstance(row, dict):
            raise TypeError("record is not an object")
        raw_note = row.get("note")
        if not isinstance(raw_note, str):
            raise TypeError("note is not a JSON string")
        raw_tags = row.get("tags") or []
        if not isinstance(raw_tags, list):
            raise TypeError("tags is not a list")
        persisted_tags = tuple(tag_names[tag] for tag in raw_tags)
        sources = row.get("sources") or []
        if not isinstance(sources, list) or len(sources) < 3:
            raise TypeError("sources is not an ordered list")
        if sources[-1] != annotation_source:
            raise ValueError("record is missing its private star type source")
        # The final source scopes the base MomentAnnotation to our custom type.
        app_sources = tuple(sources[:-1])
        envelope = StoredStarNote.model_validate_json(raw_note)
        if persisted_tags != tuple(_fulcra_tag(tag) for tag in envelope.tags):
            raise ValueError("record tags do not match the stored projection")
        # Validate in JSON mode because UUIDs and datetimes necessarily cross
        # the HTTP boundary as strings. Strict mode still rejects coercions in
        # Python callers while permitting their canonical JSON encodings.
        return GitHubStarredRepository.model_validate_json(
            json.dumps({
                "record_id": row["id"],
                "recorded_at": row["recorded_at"],
                "note": envelope.projection.model_dump(mode="json"),
                "tags": envelope.tags,
                "sources": app_sources,
            })
        )

    def create_profile_share(
        self,
        *,
        name: str,
        allowed_user_ids: Sequence[str],
        data_types: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Create a direct share after enforcing profile-only data access."""
        types = self._types or self.ensure_private_types()
        selected = tuple(data_types) if data_types is not None else (types.profile,)
        self._assert_profile_only_share(selected, share_all=False)
        return self._client.create_datashare(
            datashare_name=name,
            fulcra_data_types=list(selected),
            allowed_user_ids=sorted(set(allowed_user_ids)),
            share_all_data=False,
            time_start=None,
            time_end=None,
        )

    def _assert_profile_only_share(
        self, data_types: Sequence[str], *, share_all: bool
    ) -> None:
        types = self._types or self.ensure_private_types()
        if share_all or not data_types or any(item != types.profile for item in data_types):
            raise UnsafeShareError(
                "Build Chemistry shares must contain only BuilderTasteProfile; "
                "GitHubStarredRepository records are private"
            )


def _ordered_unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _fulcra_tag(value: str) -> str:
    """Fit a useful logical tag into Fulcra's 30-character tag boundary."""
    if len(value) <= MAX_FULCRA_TAG_LENGTH:
        return value
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{value[: MAX_FULCRA_TAG_LENGTH - 9]}-{digest}"


def _validate_window(start_time: datetime, end_time: datetime) -> None:
    for name, value in (("start_time", start_time), ("end_time", end_time)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone")
    if start_time >= end_time:
        raise ValueError("start_time must be before end_time")
