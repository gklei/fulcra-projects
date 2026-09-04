"""Compatible profile selection and deterministic Build Chemistry reveals."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from .models import BuilderTasteProfile, LensName, StrictModel


class MissingProfileError(ValueError):
    """One or both participants have no saved profile to compare."""


class IncompatibleProfilesError(ValueError):
    """The participants have profiles, but no shared lens contract."""


class ComparisonPersonality(StrictModel):
    github_username: str = Field(min_length=1, max_length=39)
    personality_name: str = Field(min_length=1, max_length=80)
    blurb: str = Field(min_length=1, max_length=500)


class BuildChemistryResult(StrictModel):
    """The five display fields of a comparison card."""

    schema_version: Literal[1] = 1
    personalities: tuple[ComparisonPersonality, ComparisonPersonality]
    shared_obsession: str = Field(min_length=1, max_length=300)
    productive_disagreement: str = Field(min_length=1, max_length=300)
    build_idea: str = Field(min_length=1, max_length=300)
    chemistry_verdict: str = Field(min_length=1, max_length=200)
    lens: LensName
    lens_version: str = Field(pattern=r"^v[0-9]+$")


def select_compatible_profiles(
    first: Sequence[BuilderTasteProfile],
    second: Sequence[BuilderTasteProfile],
) -> tuple[BuilderTasteProfile, BuilderTasteProfile]:
    """Select the freshest symmetric pair with the same schema and lens version.

    Each owner's newest snapshot under a compatible contract is considered. If
    several contracts are common, the contract with the newest older snapshot
    wins, then the newer snapshot and stable contract name break ties. This is
    independent of which participant opens the card.
    """
    if not first and not second:
        raise MissingProfileError("Neither builder has a saved profile yet.")
    if not first:
        raise MissingProfileError("You need a saved profile before comparing.")
    if not second:
        raise MissingProfileError("The other builder needs a saved profile before comparing.")

    def contract(profile: BuilderTasteProfile) -> tuple[int, str, str]:
        return profile.schema_version, profile.lens, profile.lens_version

    first_by_contract = _latest_by_contract(first, contract)
    second_by_contract = _latest_by_contract(second, contract)
    common = first_by_contract.keys() & second_by_contract.keys()
    if not common:
        raise IncompatibleProfilesError(
            "Your saved profiles use different lenses or versions. Both builders must save a profile with the same preset lens and version."
        )

    def choice_key(key: tuple[int, str, str]) -> tuple[object, ...]:
        left, right = first_by_contract[key], second_by_contract[key]
        instants = sorted((left.generated_at, right.generated_at))
        return instants[0], instants[1], key

    selected = max(common, key=choice_key)
    return first_by_contract[selected], second_by_contract[selected]


def _latest_by_contract(profiles, contract):
    latest: dict[tuple[int, str, str], BuilderTasteProfile] = {}
    for profile in profiles:
        key = contract(profile)
        current = latest.get(key)
        if current is None or (profile.generated_at, str(profile.record_id)) > (
            current.generated_at,
            str(current.record_id),
        ):
            latest[key] = profile
    return latest


def generate_comparison(
    first: BuilderTasteProfile, second: BuilderTasteProfile
) -> BuildChemistryResult:
    """Create a bounded five-field card from two compatible compact profiles."""
    if (
        first.schema_version,
        first.lens,
        first.lens_version,
    ) != (
        second.schema_version,
        second.lens,
        second.lens_version,
    ):
        raise IncompatibleProfilesError(
            "The selected profiles do not use the same lens and version."
        )

    # Canonical ordering makes the reveal identical from either participant's view.
    left, right = sorted(
        (first, second),
        key=lambda profile: (
            profile.github_username.casefold(),
            profile.github_username,
            str(profile.record_id),
        ),
    )
    shared = _shared_signal(left, right)
    left_language = _top_name(left.features.language_counts, "many languages")
    right_language = _top_name(right.features.language_counts, "many languages")

    if left_language.casefold() != right_language.casefold():
        disagreement = (
            f"@{left.github_username} reaches first for {left_language}; "
            f"@{right.github_username} favors {right_language}. That tension can keep the stack honest."
        )
    elif left.features.fork_count != right.features.fork_count:
        disagreement = (
            f"They agree on {left_language}, but @{left.github_username} and "
            f"@{right.github_username} show different appetites for remixing forks."
        )
    else:
        disagreement = (
            "Their disagreement is likely to be about how quickly to turn an interesting tool "
            "into dependable infrastructure."
        )

    idea = (
        f"Build a small {shared} workshop that combines {left_language} with "
        f"{right_language}, then publish the useful parts for other builders."
    )
    verdict = (
        "Strong workshop chemistry: enough overlap to start quickly, with enough contrast "
        "to avoid building the obvious thing."
    )
    left_personality = ComparisonPersonality(
        github_username=left.github_username,
        personality_name=left.personality_name,
        blurb=left.blurb,
    )
    right_personality = ComparisonPersonality(
        github_username=right.github_username,
        personality_name=right.personality_name,
        blurb=right.blurb,
    )
    return BuildChemistryResult(
        personalities=(left_personality, right_personality),
        shared_obsession=(
            f"Both star trails keep returning to {shared}. It is the clearest place their curiosity overlaps."
        ),
        productive_disagreement=disagreement,
        build_idea=idea,
        chemistry_verdict=verdict,
        lens=left.lens,
        lens_version=left.lens_version,
    )


def _top_name(counts, fallback: str) -> str:
    return counts[0].name if counts else fallback


def _shared_signal(first: BuilderTasteProfile, second: BuilderTasteProfile) -> str:
    first_topics = {row.name.casefold(): row for row in first.features.topic_counts}
    topic_candidates = [
        (row.count + first_topics[row.name.casefold()].count, row.name)
        for row in second.features.topic_counts
        if row.name.casefold() in first_topics
    ]
    if topic_candidates:
        return max(topic_candidates, key=lambda item: (item[0], item[1].casefold()))[1]

    first_languages = {row.name.casefold(): row for row in first.features.language_counts}
    language_candidates = [
        (row.count + first_languages[row.name.casefold()].count, row.name)
        for row in second.features.language_counts
        if row.name.casefold() in first_languages
    ]
    if language_candidates:
        return max(language_candidates, key=lambda item: (item[0], item[1].casefold()))[1]
    return "developer tools"
