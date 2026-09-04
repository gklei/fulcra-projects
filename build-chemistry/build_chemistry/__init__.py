"""Public API for the Build Chemistry projection library."""

from .comparison import (
    BuildChemistryResult,
    ComparisonPersonality,
    IncompatibleProfilesError,
    MissingProfileError,
    generate_comparison,
    select_compatible_profiles,
)
from .errors import (
    GitHubAPIError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    InsufficientPublicDataError,
    InvalidGitHubResponseError,
)
from .fulcra_context import (
    ContextConfigurationError,
    ContextTypes,
    FulcraContextAdapter,
    InvalidStoredRecordError,
    UnsafeShareError,
)
from .github import GitHubClient
from .lenses import extract_features, generate_profile, regenerate_profile, source_fingerprint
from .models import (
    BuilderTasteFeatures,
    BuilderTasteProfile,
    FeatureCount,
    GitHubStarredRepository,
    LensName,
    ProjectionNote,
    StarProjection,
)
from .projection import deterministic_record_id, normalize_username, project_public_stars

__all__ = [
    "BuildChemistryResult",
    "ComparisonPersonality",
    "ContextConfigurationError",
    "ContextTypes",
    "BuilderTasteFeatures",
    "BuilderTasteProfile",
    "FeatureCount",
    "FulcraContextAdapter",
    "GitHubAPIError",
    "GitHubClient",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubStarredRepository",
    "InsufficientPublicDataError",
    "IncompatibleProfilesError",
    "InvalidGitHubResponseError",
    "InvalidStoredRecordError",
    "LensName",
    "MissingProfileError",
    "ProjectionNote",
    "StarProjection",
    "UnsafeShareError",
    "deterministic_record_id",
    "extract_features",
    "generate_profile",
    "generate_comparison",
    "normalize_username",
    "project_public_stars",
    "regenerate_profile",
    "select_compatible_profiles",
    "source_fingerprint",
]