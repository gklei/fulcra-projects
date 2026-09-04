from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import Route, sync_playwright

from app.readiness import readiness_errors
from app.web import create_app


def test_readiness_finds_resources_and_no_source_credentials() -> None:
    assert readiness_errors() == []


def test_health_is_credential_free_and_responses_have_safe_headers() -> None:
    def forbidden_auth() -> NoReturn:
        raise AssertionError("health must not inspect credentials")

    client = TestClient(create_app(fulcra_client_factory=forbidden_auth))
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"


def test_invalid_form_is_accessible_html_and_does_not_echo_input() -> None:
    client = TestClient(create_app())
    secret_like_input = "do-not-echo-this-value" * 4
    response = client.post(
        "/profiles/save",
        data={"username": secret_like_input, "lens": "default"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Check your request" in response.text
    assert secret_like_input not in response.text
    assert '<html lang="en">' in response.text
    assert '<main id="main"' in response.text


def test_invalid_card_query_does_not_echo_input() -> None:
    client = TestClient(create_app())
    submitted_value = "do-not-echo!"

    response = client.get("/card", params={"username": submitted_value})

    assert response.status_code == 400
    assert submitted_value not in response.text


def test_auth_failure_does_not_echo_or_log_credential_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    credential_detail = "access-token-do-not-disclose"

    def failed_auth() -> NoReturn:
        raise RuntimeError(credential_detail)

    client = TestClient(create_app(fulcra_client_factory=failed_auth))
    response = client.get("/fulcra")

    assert response.status_code == 401
    assert credential_detail not in response.text
    assert credential_detail not in caplog.text


def test_responsive_and_accessibility_fallback_styles_are_shipped() -> None:
    css = (Path(__file__).parents[1] / "static" / "app.css").read_text()
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 380px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "@media (forced-colors: active)" in css


def test_home_works_in_a_narrow_keyboard_driven_browser() -> None:
    """Exercise the rendered app and stylesheet in a real browser engine."""
    client = TestClient(create_app())

    def serve(route: Route) -> None:
        parsed = urlsplit(route.request.url)
        response = client.get(parsed.path or "/")
        route.fulfill(
            status=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type", "text/plain"),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 360, "height": 740})
        page.route("http://build-chemistry.test/**", serve)
        page.goto("http://build-chemistry.test/")

        assert page.get_by_role("heading", name="Meet your Builder Personality").is_visible()
        assert page.get_by_label("GitHub username").is_visible()
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )

        page.keyboard.press("Tab")
        assert page.locator(".skip-link").evaluate(
            "element => element === document.activeElement"
        )
        page.keyboard.press("Enter")
        assert page.locator("#main").evaluate(
            "element => element === document.activeElement"
        )
        browser.close()