---
type: Architecture
project: Build Chemistry
status: approved
---

# Build Chemistry Architecture

## 1. Architectural intent

Build Chemistry has two deliberately different paths:

```text
Solo path
GitHub username -> public stars -> Builder Personality -> shareable solo card
(no authentication)

Comparison path
Person A saves profile to Fulcra <-> Person B saves profile to Fulcra
             explicit direct shares in both directions
                              ->
                 Build Chemistry comparison card
```

The solo path makes the idea instantly understandable. After Fulcra authentication, the same bounded public input is stored as a private, normalized context projection. Users can regenerate personalities from that projection through versioned lenses. The comparison path makes Fulcra essential by requiring user-owned derived profiles and explicit, revocable sharing before the joint reveal.

## 2. Smallest viable system

Use one web application with a stateless application service and no application database.

- The browser provides the UI, shareable solo-card URLs, and pair invitation flow.
- The application service fetches bounded public GitHub data, normalizes it, extracts deterministic features, and generates playful language from versioned presets.
- Fulcra owns authenticated star projections, lens-specific profile snapshots, and direct-share permissions.
- A short-lived in-memory cache may reduce duplicate GitHub requests; it is not durable user context.
- Public cards contain rendered output and provenance labels, not Fulcra credentials or private records.

Before authentication, GitHub star lists are processed transiently. When a user explicitly saves their context, the app writes a bounded normalized projection to Fulcra. Those records are private and are never included in the direct share used for comparison.

## 3. Capability map

| Need | Capability | Decision |
|---|---|---|
| Immediate public input | GitHub REST `GET /users/{username}/starred` | No GitHub auth; bounded recent sample |
| Reusable source context | Fulcra custom `GitHubStarredRepository` records | Private bounded projection; one event per star |
| Playful personality | Versioned deterministic interpreter | Bounded feature extraction followed by preset language generation |
| Customizable regeneration | Versioned lens registry | Small preset set in v1; no arbitrary prompts |
| Durable owner-controlled profile | Fulcra custom recordable data type | One lens-specific `BuilderTasteProfile` snapshot per generation |
| Explicit two-person access | Fulcra direct share | Share only the profile data type with one known Fulcra user ID |
| Read friend's profile | Fulcra shared dataset query | Query with shared type ID and `--user-id`/SDK equivalent |
| Revoke access | Fulcra share delete/update or recipient leave | Exposed as “disconnect” in the experience |
| Public result | Rendered web card | Clearly labeled as generated from public GitHub stars |

Live CLI verification established that direct shares support selected data types, recipient user IDs, time bounds, incoming/outgoing listings, updates, deletion, and leaving. Shared records can be retrieved for a specific sharing user with `get-records ... --user-id`.

## 4. Data architecture

### 4.1 `GitHubStarredRepository`

Create a private user-defined data type derived from `MomentAnnotation`, with one record for each repository in the bounded projection.

Why `MomentAnnotation`:

- A GitHub star is an event at a real historical instant.
- Repository metadata is multi-dimensional and does not fit a metric `value` field.
- A duration and the numeric/scale/boolean base types do not match the source semantics.

Proposed `note` JSON:

```json
{
  "schema_version": 1,
  "github_username": "example",
  "repository_id": 123,
  "repository_node_id": "...",
  "full_name": "owner/repository",
  "html_url": "https://github.com/owner/repository",
  "description": "...",
  "primary_language": "TypeScript",
  "topics": ["local-first", "developer-tools"],
  "owner_login": "owner",
  "is_fork": false,
  "is_archived": false,
  "observed_at": "...",
  "projection_version": "v1"
}
```

Do not store README bodies, source code, GitHub credentials, or the complete upstream API payload.

#### `recorded_at` semantics

Use GitHub's actual `starred_at` timestamp, requested through the starred-repository representation of the public API. This makes Fulcra time-range queries reflect when interest was expressed rather than when ingestion happened. `observed_at` separately records when the projection was refreshed.

#### Tags

Create real tags for useful cross-cutting dimensions:

- `build-chemistry-star`
- `star-projection-v1`
- `github-user:<normalized-username>`
- `language:<normalized-primary-language>` when present
- `topic:<normalized-topic>` for a bounded topic set
- `repository:<normalized-owner-and-name>`

Description and URLs remain in the note because they are content, not filtering dimensions.

#### Sources

Use the ordered provenance chain:

```text
github:stars:<normalized-username>
build-chemistry:star-projector:v1
```

Use a deterministic record UUID derived from the Fulcra owner, GitHub repository ID, and `starred_at` so retries are idempotent. A refresh writes only normalized records in the approved sample bound. Reconciliation of later unstars is deferred; the event still truthfully records that the repository was starred at that historical time.

### 4.2 `BuilderTasteProfile`

Create one user-defined data type derived from `MomentAnnotation`.

Why `MomentAnnotation`:

- The profile is a multi-dimensional snapshot, not one numeric, boolean, or scale value.
- `NumericAnnotation`, `ScaleAnnotation`, and `BooleanAnnotation` have real `value` fields, but reducing the profile to one scalar would create fake precision.
- `DurationAnnotation` represents a historical interval and does not match a generated snapshot.
- `MomentAnnotation` correctly supports one timestamp plus structured content in `note`.

Proposed `note` JSON:

```json
{
  "schema_version": 1,
  "github_username": "example",
  "sample": {
    "repository_count": 100,
    "newest_star_at": "...",
    "oldest_star_at": "..."
  },
  "dominant_technologies": ["..."],
  "repository_themes": ["..."],
  "experimental_practical_tendency": "...",
  "favorite_project_kind": "...",
  "personality_name": "...",
  "personality_blurb": "...",
  "lens": {
    "id": "default",
    "version": 1
  },
  "profile_fingerprint": "...",
  "generator_version": "v1"
}
```

The note excludes the complete repository list and internal generation details.

#### `recorded_at` semantics

`recorded_at` is the instant the profile snapshot was generated. This is not an accidental ingestion timestamp: the record genuinely represents the user's derived profile under a specific lens as of that generation. The actual star timestamps live on the source projection records and are summarized by `newest_star_at` and `oldest_star_at` in the profile note.

#### Tags

Create and attach real Fulcra tags for dimensions consumers may filter or group by:

- `build-chemistry`
- `builder-profile-v1`
- `github-user:<normalized-username>`
- `lens:<normalized-lens-id>-v<version>`
- `archetype:<normalized-personality-name>`

The first two are stable operational dimensions. Username and archetype are useful retrieval/grouping dimensions. Rich feature arrays remain in the note because they are multi-valued profile content rather than a small stable taxonomy.

#### Sources

Use an ordered provenance chain:

```text
github:stars:<normalized-username>
fulcra:GitHubStarredRepository:v1
build-chemistry:feature-extractor:v1
build-chemistry:lens:<lens-id>:v<version>
build-chemistry:personality-generator:v1
```

This distinguishes public source data, deterministic transformation, and the generated interpretation.

### 4.3 `BuildChemistryResult`

Do not create a second Fulcra type for v1. The comparison is computed from two shared profile snapshots and rendered as a card. A later version may add an explicit “Save this result to my context” action as another `MomentAnnotation`-derived snapshot, but the application must not silently write a joint interpretation into either participant's context.

## 5. Ownership and tenancy

- Each participant owns their `BuilderTasteProfile` records in their own Fulcra datastore.
- Each participant also owns their private `GitHubStarredRepository` projection; it is not included in the comparison share.
- Neither participant writes to the other's datastore.
- Each participant directly shares only their profile data type with the other participant.
- Both directional shares must be active before comparison.
- A comparison reads the newest compatible profile from each owner.
- Leaving or deleting a share prevents future comparison reads; already exported public cards cannot be remotely revoked.
- The application has no shared writable user database and does not merge participant ownership.

## 6. Pairing flow

1. A visitor enters any public GitHub username and receives a transient solo card under the default lens.
2. To save or customize it, they authenticate to Fulcra and explicitly import the bounded normalized star projection.
3. They select a versioned lens and generate a `BuilderTasteProfile` from their private projection.
4. The app creates a pairing link containing non-secret routing metadata: inviter Fulcra user ID, GitHub username, profile schema/lens version, and profile data-type ID.
5. The friend opens the link, authenticates to Fulcra, imports their own bounded projection, and generates a compatible lens-specific profile.
6. Each participant explicitly creates a direct share of only their `BuilderTasteProfile` type to the other Fulcra user ID.
7. The app verifies both incoming/outgoing permissions and retrieves the selected compatible profiles.
8. The application generates the joint card from those two compact profiles.

The pairing link contains no Fulcra access token, GitHub credential, raw star list, or private record.

## 7. Public sharing and attribution

Solo and comparison cards may be shared publicly. Every card must state:

> Generated from public GitHub star activity. This is a playful automated interpretation and does not imply the GitHub account owner's participation or approval.

A card uses GitHub usernames as source labels, not verified identity claims. Fulcra participation proves control of a Fulcra account and consent to share the saved profile; it does not prove ownership of the entered GitHub username because v1 deliberately avoids GitHub authentication.

## 8. Generation boundary

The personality and comparison generators receive only a compact feature summary or two compact profiles. They do not receive:

- GitHub or Fulcra credentials;
- complete Fulcra account context;
- unrelated Fulcra data types;
- private repositories;
- unrestricted tool access.

Deterministic code performs fetching, normalization, sampling, feature counting, schema validation, profile fingerprinting, and bounded card generation.

### Lens contract

A lens is a versioned, code-reviewed interpretation preset that changes emphasis and tone while preserving the output schema. V1 ships a small curated registry: `default`, `chaotic-collaborator`, and `pragmatic-builder`. A profile records the exact lens ID/version and a fingerprint of its source records. Free-form customization is deferred so output remains reproducible and testable.

## 9. Failure behavior

- Unknown/private GitHub username: explain that no public profile can be generated.
- Hidden or empty stars: produce a clear insufficient-public-data state, not invented analysis.
- GitHub rate limit: show a retry time; use bounded caching and no silent authenticated fallback.
- Fulcra auth incomplete: preserve the solo result locally for the session but do not claim it was saved.
- Star projection incomplete: do not derive a saved profile; report the failed records and permit an idempotent retry.
- Missing reciprocal share: show which consent step remains; do not generate the comparison.
- Incompatible profile version: ask the participant to regenerate rather than guessing fields.

## 10. Gap register

| Gap/risk | v1 treatment |
|---|---|
| GitHub unauthenticated limit is 60 requests/hour per service IP | Bound requests, cache briefly, surface rate-limit errors; add GitHub App credentials only if real usage requires it |
| Projection refresh can see duplicate stars | Deterministic record IDs make writes idempotent |
| A repository may later be unstarred | Preserve the truthful historical star event in v1; add active-state reconciliation only if needed |
| Direct shares require known Fulcra user IDs | Pair link exchanges IDs as non-secret routing metadata |
| Custom type IDs may differ between owners | Pair metadata carries each owner's type ID; shared catalog/query remains owner-scoped |
| Fulcra identity does not prove GitHub ownership | Explicit non-verified attribution on every card |
| Public cards cannot be revoked after export | State this at share time; revocation only controls future Fulcra reads |
| Interpretation output can change | Versioned presets, strict schemas, bounded inputs, and deterministic tests |
| Lens customization can create scope and safety problems | Ship only a small versioned preset registry in v1; defer arbitrary prompts |
| No durable result record in v1 | Deliberate simplification; add only after the core sharing loop is proven |

## 11. Security and privacy boundary

- Never request a GitHub token in v1.
- Never use `--share-all`.
- Never include `GitHubStarredRepository` in a direct share.
- Never place access tokens in pair links, cards, logs, generation inputs, or Fulcra notes.
- Never infer that a public username belongs to the authenticated Fulcra user.
- Require explicit save and explicit reciprocal share actions.
- Query only the shared profile type and intended participant ID.
- Keep application logs free of profile notes by default.

## 12. Architecture acceptance criteria

The architecture is proven when two real Fulcra users can:

1. generate solo personalities from real public GitHub stars without GitHub authentication;
2. save bounded, normalized star-event projections privately in their own contexts with correct historical timestamps, tags, and provenance;
3. regenerate valid `BuilderTasteProfile` snapshots under at least two versioned lenses without refetching GitHub;
4. directly share only those profiles with each other;
5. retrieve compatible lens-specific profiles through active reciprocal shares;
6. generate a schema-valid Build Chemistry card;
7. revoke a share and prevent a fresh comparison read;
8. complete all of the above without exposing projected star records to the other participant.
