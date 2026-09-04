"""FastAPI entry point for the no-auth solo card and explicit Fulcra save flow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

from app.fulcra_client import get_fulcra_client
from build_chemistry.errors import (
    GitHubAPIError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    InsufficientPublicDataError,
    InvalidGitHubResponseError,
)
from build_chemistry.comparison import (
    IncompatibleProfilesError,
    MissingProfileError,
    generate_comparison,
)
from build_chemistry.fulcra_context import FulcraContextAdapter, InvalidStoredRecordError
from build_chemistry.lenses import generate_profile
from build_chemistry.models import BuilderTasteProfile, LensName, StarProjection
from build_chemistry.pairing import (
    InvalidPairLinkError,
    PairNotReadyError,
    PairRoute,
    PairingService,
)
from build_chemistry.projection import normalize_username, project_public_stars

APP_DIR = Path(__file__).parent
ATTRIBUTION = (
    "Generated from public GitHub star activity. This is a playful automated "
    "interpretation and does not imply the GitHub account owner's participation or approval."
)
LENSES: tuple[LensName, ...] = (
    "default",
    "chaotic-collaborator",
    "pragmatic-builder",
)
LENS_LABELS: dict[LensName, str] = {
    "default": "Default",
    "chaotic-collaborator": "Chaotic collaborator",
    "pragmatic-builder": "Pragmatic builder",
}
Projector = Callable[..., StarProjection]
ClientFactory = Callable[[], Any]
PROFILE_READ_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
PROFILE_READ_END = datetime(2100, 1, 1, tzinfo=timezone.utc)


def create_app(
    *,
    projector: Projector = project_public_stars,
    fulcra_client_factory: ClientFactory = get_fulcra_client,
) -> FastAPI:
    """Create an app with injectable network boundaries for deterministic tests."""
    web = FastAPI(title="Build Chemistry", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=APP_DIR / "templates")
    web.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    def page(
        request: Request,
        template: str,
        *,
        status_code: int = 200,
        **context: object,
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name=template,
            context={
                "attribution": ATTRIBUTION,
                "lens_labels": LENS_LABELS,
                "lenses": LENSES,
                **context,
            },
            status_code=status_code,
        )

    @web.middleware("http")
    async def safe_response_headers(request: Request, call_next: Callable[..., Any]) -> Response:
        """Keep profile pages out of caches and apply baseline browser protections."""
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @web.exception_handler(RequestValidationError)
    def invalid_request(request: Request, _exc: RequestValidationError) -> HTMLResponse:
        """Do not expose framework validation details or submitted values."""
        return page(
            request,
            "error.html",
            status_code=400,
            title="Check your request",
            message="A required value was missing or invalid. Go back and try again.",
            username="",
        )

    def public_profile(username: str) -> tuple[StarProjection, BuilderTasteProfile]:
        projection = projector(username)
        return projection, generate_profile(projection.records, "default")

    def projection_error(request: Request, exc: Exception, username: str) -> HTMLResponse:
        if isinstance(exc, (GitHubNotFoundError, InsufficientPublicDataError)):
            message = "We could not find enough public star activity for that GitHub username."
            status = 404
        elif isinstance(exc, GitHubRateLimitError):
            retry = f" Try again after {exc.reset_at:%H:%M UTC}." if exc.reset_at else " Try again soon."
            message = "GitHub's public request limit has been reached." + retry
            status = 429
        elif isinstance(exc, ValueError):
            message = "Enter a valid GitHub username."
            status = 400
        else:
            message = "GitHub could not provide a usable star list. Please try again."
            status = 502
        return page(
            request,
            "error.html",
            status_code=status,
            title="Card unavailable",
            message=message,
            # Failure pages never reflect request input. Besides avoiding
            # accidental disclosure, this keeps malformed values out of HTML.
            username="",
        )

    @web.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request, "index.html")

    @web.get("/healthz", include_in_schema=False)
    def health() -> JSONResponse:
        """Credential-free process readiness check."""
        return JSONResponse({"status": "ok"})

    @web.get("/card", response_class=HTMLResponse)
    def card_redirect(request: Request, username: str) -> Response:
        """Keep username entry functional when browser scripting is disabled."""
        try:
            normalized = normalize_username(username)
        except ValueError as exc:
            return projection_error(request, exc, username)
        return RedirectResponse(f"/u/{normalized}", status_code=303)

    @web.get("/u/{username}", response_class=HTMLResponse)
    def solo_card(request: Request, username: str) -> HTMLResponse:
        try:
            projection, profile = public_profile(username)
        except (
            GitHubAPIError,
            InsufficientPublicDataError,
            InvalidGitHubResponseError,
            ValueError,
        ) as exc:
            return projection_error(request, exc, username)
        return page(
            request,
            "card.html",
            projection=projection,
            profile=profile,
            title=f"{profile.github_username}'s Builder Personality",
        )

    @web.get("/fulcra", response_class=HTMLResponse)
    def fulcra_status(request: Request, username: str = "") -> HTMLResponse:
        """Check the SDK credential chain without writing any context."""
        try:
            client = fulcra_client_factory()
            user_id = client.get_fulcra_userid()
        except Exception:
            # Do not expose credential paths, token errors, or SDK response bodies.
            return page(
                request,
                "authenticate.html",
                status_code=401,
                authenticated=False,
                username=username,
                title="Connect Fulcra",
            )
        return page(
            request,
            "authenticate.html",
            authenticated=True,
            username=username,
            user_label=f"{user_id[:8]}…" if len(user_id) > 8 else user_id,
            title="Fulcra connected",
        )

    @web.get("/pair", response_class=HTMLResponse)
    def create_pair_link(request: Request) -> HTMLResponse:
        """Create a metadata-only link for another authenticated Fulcra user."""
        try:
            service = PairingService(fulcra_client_factory())
            my_token = service.my_route().token()
        except Exception:
            return page(
                request,
                "authenticate.html",
                status_code=401,
                authenticated=False,
                username="",
                title="Connect Fulcra to pair",
            )
        return page(
            request,
            "pair.html",
            status=None,
            peer=None,
            pair_token=None,
            my_pair_path=f"/pair/{my_token}",
            title="Pair with another builder",
        )

    def pair_status_page(request: Request, token: str) -> HTMLResponse:
        try:
            peer = PairRoute.from_token(token)
            service = PairingService(fulcra_client_factory())
            status = service.status(peer)
            my_token = service.my_route().token()
        except InvalidPairLinkError:
            return page(
                request,
                "error.html",
                status_code=400,
                title="Invalid pair link",
                message="Ask the other builder for a new pair link.",
                username="",
            )
        except Exception:
            return page(
                request,
                "authenticate.html",
                status_code=401,
                authenticated=False,
                username="",
                title="Connect Fulcra to pair",
            )
        return page(
            request,
            "pair.html",
            status=status,
            peer=peer,
            pair_token=token,
            my_pair_path=f"/pair/{my_token}",
            title="Pair status",
        )

    @web.get("/pair/{token}", response_class=HTMLResponse)
    def pair_status(request: Request, token: str) -> HTMLResponse:
        return pair_status_page(request, token)

    @web.post("/pair/{token}/share", response_class=HTMLResponse)
    def share_profile(request: Request, token: str) -> Response:
        try:
            peer = PairRoute.from_token(token)
            PairingService(fulcra_client_factory()).share_with(peer)
        except InvalidPairLinkError:
            return page(
                request,
                "error.html",
                status_code=400,
                title="Invalid pair link",
                message="No data was shared. Ask the other builder for a new link.",
                username="",
            )
        except Exception:
            return page(
                request,
                "error.html",
                status_code=502,
                title="Share not confirmed",
                message="Fulcra could not confirm the profile share. No star records were requested.",
                username="",
            )
        return RedirectResponse(f"/pair/{token}", status_code=303)

    @web.post("/pair/{token}/revoke", response_class=HTMLResponse)
    def revoke_profile_share(request: Request, token: str) -> Response:
        try:
            peer = PairRoute.from_token(token)
            PairingService(fulcra_client_factory()).revoke(peer)
        except InvalidPairLinkError:
            return page(
                request,
                "error.html",
                status_code=400,
                title="Invalid pair link",
                message="Ask the other builder for a new pair link.",
                username="",
            )
        except Exception:
            return page(
                request,
                "error.html",
                status_code=502,
                title="Revocation not confirmed",
                message="Fulcra could not confirm revocation. Try again before comparing.",
                username="",
            )
        return RedirectResponse(f"/pair/{token}", status_code=303)

    @web.get("/pair/{token}/compare", response_class=HTMLResponse)
    def compare_profiles(request: Request, token: str) -> HTMLResponse:
        """Freshly authorize, select compatible profiles, and render the reveal."""
        try:
            peer = PairRoute.from_token(token)
            first, second = PairingService(
                fulcra_client_factory()
            ).read_pair_profiles(peer, PROFILE_READ_START, PROFILE_READ_END)
            comparison = generate_comparison(first, second)
        except InvalidPairLinkError:
            return page(
                request,
                "error.html",
                status_code=400,
                title="Invalid pair link",
                message="Ask the other builder for a new pair link.",
                username="",
            )
        except PairNotReadyError:
            return page(
                request,
                "error.html",
                status_code=409,
                title="Comparison not ready",
                message="Both builders must keep their profile-only shares active before a comparison can be read.",
                username="",
            )
        except MissingProfileError as exc:
            return page(
                request,
                "error.html",
                status_code=409,
                title="Saved profile missing",
                message=str(exc),
                username="",
            )
        except IncompatibleProfilesError:
            return page(
                request,
                "error.html",
                status_code=409,
                title="Profiles do not match",
                message="Both builders must save a profile with the same preset lens and version, then try again.",
                username="",
            )
        except InvalidStoredRecordError:
            return page(
                request,
                "error.html",
                status_code=422,
                title="Shared profile is invalid",
                message="A shared profile could not be verified. Its owner should save a fresh profile before trying again.",
                username="",
            )
        except Exception:
            return page(
                request,
                "error.html",
                status_code=502,
                title="Comparison unavailable",
                message="Fulcra could not complete a fresh profile read. No comparison was generated.",
                username="",
            )
        return page(
            request,
            "comparison.html",
            comparison=comparison,
            pair_token=token,
            title="Build Chemistry reveal",
        )

    @web.post("/profiles/save", response_class=HTMLResponse)
    def save_profile(
        request: Request,
        username: str = Form(min_length=1, max_length=39),
        lens: str = Form(default="default"),
    ) -> HTMLResponse:
        """Explicitly import private stars and save one derived profile."""
        if lens not in LENSES:
            return page(
                request,
                "error.html",
                status_code=400,
                title="Unknown lens",
                message="Choose one of the available personality lenses.",
                username=username,
            )
        selected_lens: LensName = lens  # type: ignore[assignment]
        try:
            client = fulcra_client_factory()
            owner_id = client.get_fulcra_userid()
        except Exception:
            return page(
                request,
                "authenticate.html",
                status_code=401,
                authenticated=False,
                username=username,
                title="Connect Fulcra before saving",
            )

        try:
            projection = projector(username, owner_id=owner_id)
            profile = generate_profile(projection.records, selected_lens)
            context = FulcraContextAdapter(client)
            context.write_stars(projection)
            context.write_profile(profile)
        except (
            GitHubAPIError,
            InsufficientPublicDataError,
            InvalidGitHubResponseError,
            ValueError,
        ) as exc:
            return projection_error(request, exc, username)
        except Exception:
            return page(
                request,
                "error.html",
                status_code=502,
                title="Save incomplete",
                message="Fulcra could not confirm the complete save. Some private records may have been submitted; retrying is safe because their identities are deterministic.",
                username=username,
            )

        return page(
            request,
            "saved.html",
            projection=projection,
            profile=profile,
            title="Profile saved privately",
        )

    return web


app = create_app()
