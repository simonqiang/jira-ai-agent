# Jira user and project setup

Use this guide to grant the Scrum Master Jira Assistant the least access needed
for its personal-pilot features: ticket review, search, collection, sprint reports,
approved ticket creation, and confirmed ticket updates.

The agent runs as the Jira account that owns `SCRUM_AGENT_JIRA_API_TOKEN`. It
cannot grant itself additional access. Token scopes and Jira permissions both
apply: the account must satisfy each of them.

## Before you start

- Use a dedicated human or service account for the pilot where practical.
- Give it Jira Software product access.
- Restrict the agent configuration to one intended project and one intended board:
  `SCRUM_AGENT_JIRA_PROJECT_KEY` and `SCRUM_AGENT_JIRA_BOARD_ID`.
- Use a scoped API token and an expiry appropriate to the pilot. Store it only
  in the local `.env`; never put it in source control, logs, tickets, or chat.

## Create the scoped token

1. Sign in at <https://id.atlassian.com/manage-profile/security/api-tokens>.
2. Choose **Create API token with scopes**.
3. Give the token a descriptive name, such as `scrum-agent-pilot`.
4. Select every scope in the table below.
5. Set an expiry date and save the token in a password manager before closing
   the dialog. Atlassian shows the token value only once.
6. Put the new token in `SCRUM_AGENT_JIRA_API_TOKEN` in the local `.env`.

Scoped tokens use Atlassian's central API endpoint. Keep the following settings
when using one:

```env
SCRUM_AGENT_JIRA_AUTH_MODE=basic_central
SCRUM_AGENT_JIRA_CLOUD_ID=<your-cloud-id>
SCRUM_AGENT_JIRA_API_TOKEN=<scoped-token>
```

## Required token scopes

Select these scopes for the complete current feature set.

| Scope | Why the agent needs it |
| --- | --- |
| `read:jira-work` | Read issues, run JQL search, collect changelogs, validate ticket quality, create sprint reports, and retrieve create metadata. |
| `read:jira-user` | Resolve assignee names when a ticket search includes an assignee. |
| `read:board-scope:jira-software` | Read the configured Jira Software board. |
| `read:issue-details:jira` | Read board metadata. |
| `read:board-scope.admin:jira-software` | Read board configuration, including columns and estimation settings used in reports. |
| `read:project:jira` | Read project metadata required with board configuration and user lookup. |
| `read:sprint:jira-software` | List and retrieve sprints for sprint search and reports. |
| `write:issue:jira` | Create an approved ticket and apply a confirmed ticket update. |

The implementation intentionally combines Jira's classic read scopes with the
Jira Software granular read scopes needed by board and sprint APIs. Do not add
the broader `write:jira-work` scope: it also permits comments, worklogs, and
issue deletion, which this agent does not use.

## Required Jira permissions

Grant the token-owning account these permissions in the configured project.

| Jira permission or access | Required for |
| --- | --- |
| **Jira Software product access** | Jira Software board and sprint APIs. |
| **Browse Projects** | All issue reads, reviews, searches, changelogs, collection, and reports. |
| **Create Issues** | Creating one user-approved ticket. |
| **Edit Issues** | Confirmed edits to summary, description, acceptance criteria, and labels. |
| **Schedule Issues** | Confirmed edits to a ticket's due date. |
| **View configured board** | Board metadata and sprint listing. The account must also be able to view the board's saved filter. |
| **Board configuration access** | The agent reads done-column and estimation configuration for reports. For company-managed projects, grant the least project or board administration role that allows the account to view that board's configuration. |
| **Issue security-level access** | Required whenever issue security is enabled. The account must be allowed to view each ticket the agent reads or updates. |

The Jira workflow and field configuration must also permit the supported fields
for the relevant issue type and status. The agent currently updates only
`summary`, `description`, `acceptance_criteria`, `labels`, and `due_date`.

## Permissions and scopes not needed

Do not grant these solely for this agent:

- Delete Issues or `delete:issue:jira`
- Transition Issues
- Manage Sprints or `write:sprint:jira-software`
- Move Issues, rank issues, or board-write permissions
- Comment, attachment, worklog, or issue-link write scopes
- Jira global administration

The agent also does not update assignees, reporters, status, sprint membership,
estimates, or priority. If a future feature needs one of those operations, add
its scope and Jira permission only after documenting and reviewing that change.

## Administrator checklist

- [ ] The token-owning account has Jira Software product access.
- [ ] The account can browse the configured project and view the configured board.
- [ ] The account can view the board's saved filter and board configuration.
- [ ] The account has **Create Issues**, **Edit Issues**, and **Schedule Issues**.
- [ ] The account can access all required issue-security levels.
- [ ] The scoped token has every entry in [Required token scopes](#required-token-scopes).
- [ ] The local `.env` uses `basic_central`, contains the correct Cloud ID, and is ignored by Git.

## Verify the setup

Restart the local agent after changing `.env`, then run the normal probe:

```bash
python -m scrum_agent probe
```

It should read the configured issue and board without a `401` or `403` response.
Then verify the write path on a safe test ticket: prepare an update, inspect the
rendered before/after diff, and use its confirmation control. The agent rechecks
the ticket before writing and reports a stale proposal instead of overwriting a
concurrent change.

## References

- [Atlassian: manage scoped API tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/)
- [Atlassian: Jira platform scopes](https://developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps/)
- [Atlassian: Jira Software scopes](https://developer.atlassian.com/cloud/jira/software/scopes-for-oauth-2-3LO-and-forge-apps/)
- [Atlassian: issue API permissions and scopes](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/)
- [Atlassian: board API scopes](https://developer.atlassian.com/cloud/jira/software/rest/api-group-board/)
- [Atlassian: sprint API scopes](https://developer.atlassian.com/cloud/jira/software/rest/api-group-sprint/)
