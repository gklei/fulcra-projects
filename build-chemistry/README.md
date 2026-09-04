# Build Chemistry

Build Chemistry turns up to 100 of a public GitHub account's latest stars into
a playful Builder Personality card. A public card needs no sign-in. An
authenticated user can save normalized stars privately in Fulcra, derive a
versioned profile, and compare profiles after two users create reciprocal,
profile-only Fulcra shares.

The interpretation is automated and based on public GitHub activity. It is not
a verified statement by, or proof of participation from, the GitHub account
owner.

Try the public, credential-free experience at
[build-chemistry-app.vercel.app](https://build-chemistry-app.vercel.app).

## Clean setup

Requirements: Python 3.11 or newer and
[uv](https://docs.astral.sh/uv/getting-started/installation/). No GitHub token is
used or accepted.

```bash
git clone https://github.com/gklei/fulcra-projects.git
cd fulcra-projects/build-chemistry
uv sync --extra dev --frozen
uv run playwright install chromium
uv run python -m app.readiness
uv run pytest -q
```

`app.readiness` is credential-free. It verifies packaged web resources and
checks source text for high-confidence credential formats. The declared full
test runner is `uv run pytest -q`; it includes a Chromium accessibility and
responsive smoke test. Live tests skip with a reason unless their explicit
prerequisites are available.

## Run locally

```bash
uv run uvicorn app.web:app --host 127.0.0.1 --port 8000
curl --fail http://127.0.0.1:8000/healthz
```

Open `http://127.0.0.1:8000`. The health endpoint does not load Fulcra
credentials. Application responses use `no-store` and do not report credential
paths, tokens, upstream payloads, or exception details to the browser.

## Fulcra authentication

Saving and pairing use the `fulcra-api` SDK credential file. By default it is
`~/.config/fulcra/credentials.json`; set `FULCRA_CREDENTIALS_PATH` to select a
different file. Do not copy credentials into this repository or `.env`.

Only `BuilderTasteProfile` records are directly shared. Build Chemistry never
uses `--share-all` and never includes private `GitHubStarredRepository` records
in a share.

## Live acceptance checks

The public GitHub check is bounded and opt-in:

```bash
RUN_LIVE_GITHUB=1 uv run pytest -q -m live_github app/tests/test_live_github.py
```

The real reciprocal two-user save, comparison, privacy, and revocation flow is
documented in `docs/two-user-verification.md`. It requires credentials for two
distinct Fulcra users and removes only the temporary direct shares it creates.

## Project layout

- `build_chemistry/`: strict domain models, GitHub projection, Fulcra storage,
  lens generation, pairing, and comparison logic.
- `app/`: FastAPI web app, templates, styles, readiness check, and tests.

The independently evaluated outer control harness used to build this project is
available at
[gklei/build-chemistry-harness](https://github.com/gklei/build-chemistry-harness).