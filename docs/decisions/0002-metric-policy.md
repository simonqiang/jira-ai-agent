# ADR-0002: Sprint metric policy

Status: accepted (Week 1, 2026-09-27)
Supersedes: the provisional metric table in spec §4 (updated in the same commit as this
record; the reporting contract carries a `metric-policy version` field that references
this document).

## Context

The design reviews found that "committed/delivered at cutoff" state semantics reproduce
known reporting errors: done-before-start inflates completion, reopened work is
miscounted, and closure-time rollover is ambiguous. Week 1 must define the rules the
Week 5–6 report service implements.

## Policy

1. **Scope basis: initial planned scope.** The sprint universe is built from changelog
   membership events; *committed work* is the set in the sprint at its start, with
   estimate values as recorded at that time. Missing estimates are unknown, never zero.
2. **Two completion measures, reported separately:**
   - *Completed during sprint*: first entered a configured done state after sprint start
     **and** is in a done state at cutoff. Done-before-start issues are excluded.
   - *Done by end*: in a done state at cutoff, whenever entered (superset).
3. **Reopened work:** issues done at some point during the sprint but not done at cutoff
   are excluded from *completed during sprint*, listed separately, and counted as
   unfinished.
4. **Commitment completion** = committed issues completed during the sprint ÷ all
   committed issues. The point-based version uses start-time estimates in both numerator
   and denominator. Zero denominator is N/A, distinct from zero.
5. **Pre-closure state:** the issue state immediately before sprint close (for active
   sprints, at the observation cutoff) is recorded per issue; it is the basis for
   rollover classification and for distinguishing unfinished original commitment from
   unfinished additions and removed commitment.
6. **Rollover:** unfinished work with changelog evidence of moving into a later sprint.
   Without that evidence the label is *unfinished*, not carryover.
7. **Done resolution:** from the board's column/status mapping (versioned), never from a
   status literally named "Done". Board configuration snapshots are retained.
8. **Sprint Goal outcome:** human-confirmed or unknown; never inferred from issue data.
9. **Boundaries:** all instants are evaluated in UTC with sprint boundaries expressed in
   Asia/Hong_Kong (ADR-0001); parent/subtask estimate double-counting is off unless team
   policy enables it.
10. **Insufficient evidence:** if history cannot establish original commitment, the
    available status summary is returned with commitment/scope-change metrics marked
    unavailable or partial — never silently recomputed on current state.

## Consequences

- Spec §4's table now states these definitions; future changes require a new policy
  version (the report record carries it).
- Week 6 fixtures must cover: done-before-start, reopened-at-cutoff, pre-closure
  rollover, missing estimates, and estimate-edited-after-add.
