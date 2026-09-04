"""Strict schemas for normalized star projections."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    # These models are also an application boundary: callers must not be able
    # to turn values such as ``"123"`` into repository IDs implicitly.
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProjectionNote(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    github_username: str = Field(min_length=1, max_length=39)
    repository_id: int = Field(gt=0)
    repository_node_id: str = Field(min_length=1)
    full_name: str = Field(pattern=r"^[^/]+/[^/]+$")
    html_url: HttpUrl
    description: str | None = Field(default=None, max_length=500)
    primary_language: str | None = Field(default=None, max_length=100)
    topics: tuple[str, ...] = Field(default=(), max_length=10)
    owner_login: str = Field(min_length=1)
    is_fork: bool
    is_archived: bool
    observed_at: datetime
    projection_version: str = Field(default="v1", pattern=r"^v1$")

    @field_validator("observed_at")
    @classmethod
    def require_aware_observed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value


class GitHubStarredRepository(StrictModel):
    """Fulcra-ready normalized MomentAnnotation-shaped star event."""

    record_id: UUID
    recorded_at: datetime
    note: ProjectionNote
    tags: tuple[str, ...] = Field(min_length=4)
    sources: tuple[str, str]

    @field_validator("recorded_at")
    @classmethod
    def require_aware_recorded_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value


class StarProjection(StrictModel):
    github_username: str
    records: tuple[GitHubStarredRepository, ...] = Field(max_length=100)


class FeatureCount(StrictModel):
    """A stable, JSON-friendly count used by profile features."""

    name: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1)


class BuilderTasteFeatures(StrictModel):
    """Deterministic aggregate facts extracted from private star records."""

    schema_version: int = Field(default=1, ge=1, le=1)
    star_count: int = Field(ge=1, le=100)
    owner_count: int = Field(ge=1, le=100)
    fork_count: int = Field(ge=0, le=100)
    archived_count: int = Field(ge=0, le=100)
    language_counts: tuple[FeatureCount, ...] = Field(max_length=100)
    topic_counts: tuple[FeatureCount, ...] = Field(max_length=1000)

    @model_validator(mode="after")
    def counts_fit_source_size(self) -> "BuilderTasteFeatures":
        if any(value > self.star_count for value in (
            self.owner_count, self.fork_count, self.archived_count
        )):
            raise ValueError("owner, fork, and archived counts cannot exceed star_count")
        if sum(row.count for row in self.language_counts) > self.star_count:
            raise ValueError("language counts cannot exceed star_count")
        if sum(row.count for row in self.topic_counts) > self.star_count * 10:
            raise ValueError("topic counts cannot exceed ten per star")
        if len({row.name for row in self.language_counts}) != len(self.language_counts):
            raise ValueError("language feature names must be unique")
        if len({row.name for row in self.topic_counts}) != len(self.topic_counts):
            raise ValueError("topic feature names must be unique")
        return self


LensName = Literal["default", "chaotic-collaborator", "pragmatic-builder"]


class BuilderTasteProfile(StrictModel):
    """Versioned, share-safe output derived only from normalized star data."""

    schema_version: int = Field(default=1, ge=1, le=1)
    record_id: UUID
    generated_at: datetime
    github_username: str = Field(min_length=1, max_length=39)
    lens: LensName
    lens_version: str = Field(default="v1", pattern=r"^v1$")
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    features: BuilderTasteFeatures
    personality_name: str = Field(min_length=1, max_length=80)
    blurb: str = Field(min_length=1, max_length=500)

    @field_validator("generated_at")
    @classmethod
    def require_aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must include a timezone")
        return value