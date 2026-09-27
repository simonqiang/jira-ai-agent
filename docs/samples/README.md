# Week 1 sample collection (owner: pilot user)

The spec requires three representative ticket examples and one manually prepared sprint
report as a baseline, **anonymized before they become fixtures** (no colleague names,
customer data, credentials or real issue keys — see spec §11).

## Status (2026-09-27)

Filled examples drafted from real pilot-project tickets, paraphrased and anonymized:
[story](story.example.md) · [bug](bug.example.md) · [task](task.example.md). Each ends
with observations that seed the Week 7 templates. Remaining: pilot-user review of the
three examples, and the [manual sprint-report baseline](sprint-report-baseline.md) with
the real preparation time — the one input that cannot be synthesized.

## What to collect

1. **Three ticket examples** — one Story, one Bug, one Task from the supported board.
   For each, fill a copy of the matching template below and record:
   - What makes it good/poor (this seeds the Week 7 templates).
   - Which fields were mandatory in Jira (project, issue type, custom fields).

2. **One sprint report baseline** — a sprint report you prepared manually. Record:
   - Time taken to prepare (minutes) — the pilot's improvement baseline.
   - Which numbers you computed and where they came from.

3. **Board facts** — board type, filter scope, estimate field, done-status ID mapping
   (the probe prints metadata; transcribe only anonymized facts, never raw output).
   Replace real ticket keys, links, names and summaries with placeholders. Keep any
   necessary real board/project configuration in the local `.env` or an approved
   private decision record. A board's location does not establish its filter scope.

## Templates

### story.example.md

```markdown
Title:
As a <role>, I want <goal>, so that <benefit>.
Context / business need:
Scope: / Out of scope:
Acceptance criteria:
Dependencies:
Open questions:
```

### bug.example.md

```markdown
Title:
Environment / version:
Steps to reproduce:
Expected: / Actual:
Impact:
Evidence (links, logs — anonymized):
Verification criteria:
```

### task.example.md

```markdown
Title / objective:
Context:
Scope: / Deliverables:
Dependencies:
Completion checklist:
```

### sprint-report-baseline.md

```markdown
Sprint: / Board:
Prepared by: (role only) / Date:
Minutes spent preparing:
Committed: (count, points) / Completed: (count, points)
Scope changes: / Blockers:
Notes: what was hardest to assemble?
```

**Reminder:** once anonymized, these live in `tests/fixtures/` and `evals/` per the
roadmap; nothing colleague-identifying enters the repository.
