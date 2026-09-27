# ADR-0001: Pilot scope and authentication

Status: accepted (Week 1, 2026-09-27)
Supersedes: provisional assumptions in spec §1.

## Context

The roadmap's Week 1 requires confirming the Jira deployment, pilot authentication,
report timezone and permitted Google Cloud region before the collector (Week 4) and
reports (Week 5) are built on them.

## Decision

| Item | Decision | Notes |
|---|---|---|
| Jira deployment | **Jira Cloud** | REST v3 + Jira Software board APIs as designed. |
| Pilot authentication | **Scoped personal API token** | Self-service, per-token scopes, selectable expiry; recommended pilot expiry ≤ 90 days. OAuth 3LO is required by the shared-team phase — the client isolates auth in `Settings`, so 3LO adds a token-refresh path rather than a rewrite. |
| Auth endpoints | Token type selects the base URL | Scoped tokens must use `api.atlassian.com/ex/jira/{cloudId}`; the client supports `basic_site`, `basic_central`, `bearer_central`. |
| Report timezone | **Asia/Hong_Kong** | Stored UTC, rendered local (spec §10). |
| Google Cloud region | **asia-east2 (Hong Kong)**, pending org-policy confirmation | Only Week 4+ deployments depend on it. |
| Credential monitoring | Required from Week 4 | Expiring/expired tokens surface as a collection-freshness alarm, not silent gaps. |

## Remaining Week 1 inputs (owner: pilot user)

- Board ID, project key and board type for the supported board.
- Estimate field (story points vs. custom field) once the board is selected.
- Three anonymized ticket examples and one sprint-report baseline (`docs/samples/`).
- Monthly budget figure (informed by the Week 3 model-call measurements).

## Live probe findings (2026-09-27)

Verified against `dhl.atlassian.net` with a scoped personal token:

- Basic auth (email:token) against the **site URL is rejected (401)** for this token
  type; the same credentials succeed against the central endpoints
  `https://api.atlassian.com/ex/jira/{cloudId}` — pilot uses `basic_central`.
- cloudId for this site: recorded in `.env` (not in source control).
- Issue reads and JQL search work with the current scopes (a bounded JQL is required:
  unbounded queries are rejected with 400).
- The Jira Software **board API returns 401 "scope does not match"** — the token was
  created without board/sprint read scopes. Recreate the token adding board/sprint
  read access (or the classic `read:jira-work` scope bundle) to complete Week 1's
  board-configuration fetch.

## Consequences

- Week 1 probe and Week 2 search run against the pilot user's own token; permissions are
  Jira-native for live reads.
- Migration to 3LO (shared-team phase or earlier if desired) is an auth-module change plus
  per-user token storage, not an adapter redesign.
