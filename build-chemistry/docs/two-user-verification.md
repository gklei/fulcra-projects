# Real two-user verification

This acceptance flow uses public GitHub data and two distinct real Fulcra
accounts. It never requests a GitHub token. It writes bounded star/profile
fixtures into each owner's private context, shares only each owner's
`BuilderTasteProfile` subtype, generates a comparison from a fresh shared read,
then revokes the temporary direct shares. Private star records remain in each
owner's context and are explicitly proven unreadable by the peer.

## Automated verification

1. Authenticate the primary Fulcra user normally. Its credential path defaults
   to `~/.config/fulcra/credentials.json`; override it with
   `FULCRA_CREDENTIALS_PATH` if needed.
2. Put the second user's credential file outside the repository and export its
   path:

   ```bash
   export FULCRA_PAIR_PEER_CREDENTIALS_PATH=/secure/path/peer-credentials.json
   ```

3. From the repository root, run:

   ```bash
   uv run pytest -q -m live_fulcra \
     app/tests/test_pairing.py::test_live_two_user_reciprocal_consent_and_revocation \
     app/tests/test_comparison.py::test_live_two_shared_profiles_produce_a_valid_reveal
   ```

A passing run verifies two distinct user IDs, exact profile-only direct-share
metadata, reciprocal readiness, real shared-profile reads, denial of both star
subtypes, all five comparison fields, and denial of a fresh read after one
share is revoked. If credentials are absent, identical, or the users already
have shares between them, the tests skip rather than modifying relationships
they did not create. The tests' `finally` blocks delete only share IDs created
during that run; deterministic context record IDs make fixture retries safe.

## Browser walkthrough

Run the app once with each user's credentials (separate processes or one at a
time):

1. Each user opens a public card, chooses the same preset lens, and selects
   **Authenticate and save privately**.
2. Each opens `/pair`, sends their pair link to the other user, follows the
   other's link, and selects **Share only my profile**.
3. Refreshing either pair page shows **Ready to compare**. Select **Reveal our
   Build Chemistry** and confirm both personalities, shared obsession,
   productive disagreement, build idea, chemistry verdict, and attribution.
4. One user selects **Revoke my profile share**. A fresh comparison request is
   blocked. Previously printed, saved, or copied cards cannot be remotely
   revoked, as the UI warns.

Never paste either credential file, access token, API response, or test output
containing credentials into an issue, log, or repository file.