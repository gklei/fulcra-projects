"""Deterministic feature extraction and the three V1 preset lenses."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime
from typing import Iterable, Protocol
from uuid import UUID, uuid5

from .models import (
    BuilderTasteFeatures,
    BuilderTasteProfile,
    FeatureCount,
    GitHubStarredRepository,
    LensName,
)

PROFILE_NAMESPACE = UUID("a8cda282-03da-55e8-93e0-112d37a737da")
LENS_VERSION = "v1"


class StarReader(Protocol):
    def read_stars(
        self, start_time: datetime, end_time: datetime
    ) -> tuple[GitHubStarredRepository, ...]: ...


def _ordered_counts(values: Iterable[str]) -> tuple[FeatureCount, ...]:
    counts = Counter(values)
    return tuple(
        FeatureCount(name=name, count=count)
        for name, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0].casefold(), item[0])
        )
    )


def extract_features(
    records: Iterable[GitHubStarredRepository],
) -> BuilderTasteFeatures:
    """Aggregate normalized records without clocks, network access, or randomness."""
    stars = tuple(records)
    if not stars:
        raise ValueError("at least one stored star is required to build a profile")
    if len(stars) > 100:
        raise ValueError("a profile cannot use more than 100 stars")
    if len({star.record_id for star in stars}) != len(stars):
        raise ValueError("source star record IDs must be unique")
    return BuilderTasteFeatures(
        star_count=len(stars),
        owner_count=len({star.note.owner_login.casefold() for star in stars}),
        fork_count=sum(star.note.is_fork for star in stars),
        archived_count=sum(star.note.is_archived for star in stars),
        language_counts=_ordered_counts(
            star.note.primary_language for star in stars if star.note.primary_language
        ),
        topic_counts=_ordered_counts(topic for star in stars for topic in star.note.topics),
    )


def source_fingerprint(records: Iterable[GitHubStarredRepository]) -> str:
    """Fingerprint the complete, canonical stored inputs independent of read order."""
    stars = sorted(records, key=lambda star: str(star.record_id))
    if not stars:
        raise ValueError("at least one stored star is required to fingerprint a source")
    payload = [star.model_dump(mode="json") for star in stars]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _top(counts: tuple[FeatureCount, ...], fallback: str) -> str:
    return counts[0].name if counts else fallback


def _interpret(lens: LensName, features: BuilderTasteFeatures) -> tuple[str, str]:
    language = _top(features.language_counts, "language-agnostic tools")
    topic = _top(features.topic_counts, "useful software")
    if lens == "default":
        return (
            "Curious Toolkit Builder",
            f"Your stars lean toward {language} and {topic}, across "
            f"{features.owner_count} different builders.",
        )
    if lens == "chaotic-collaborator":
        return (
            "Stack-Hopping Collaborator",
            f"You collect ideas across {len(features.language_counts)} language ecosystems "
            f"and keep circling back to {topic}.",
        )
    return (
        "Practical Systems Builder",
        f"Your shelf favors {language}: {features.star_count - features.archived_count} of "
        f"{features.star_count} picks are active repositories.",
    )


def generate_profile(
    records: Iterable[GitHubStarredRepository],
    lens: LensName,
    *,
    generated_at: datetime | None = None,
) -> BuilderTasteProfile:
    """Apply one closed V1 preset to normalized records.

    When no generation time is supplied, the latest projection observation is
    used. This makes offline regeneration reproducible while retaining when
    the source data was observed.
    """
    stars = tuple(records)
    features = extract_features(stars)
    usernames = {star.note.github_username.casefold() for star in stars}
    if len(usernames) != 1:
        raise ValueError("all source stars must belong to one GitHub username")
    fingerprint = source_fingerprint(stars)
    personality_name, blurb = _interpret(lens, features)
    username = next(iter(usernames))
    instant = generated_at or max(star.note.observed_at for star in stars)
    record_id = uuid5(PROFILE_NAMESPACE, f"{username}:{lens}:{LENS_VERSION}:{fingerprint}")
    return BuilderTasteProfile(
        record_id=record_id,
        generated_at=instant,
        github_username=username,
        lens=lens,
        source_fingerprint=fingerprint,
        features=features,
        personality_name=personality_name,
        blurb=blurb,
    )


def regenerate_profile(
    context: StarReader,
    start_time: datetime,
    end_time: datetime,
    lens: LensName,
    *,
    generated_at: datetime | None = None,
) -> BuilderTasteProfile:
    """Regenerate exclusively from an adapter's stored Fulcra records."""
    return generate_profile(
        context.read_stars(start_time, end_time), lens, generated_at=generated_at
    )