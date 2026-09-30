"""Week 3 exit-check runner: the checked query set answered through the agent.

Each intent drives the real ADK Runner — session service, agent, tool
execution, scoped JiraClient over the fixture Jira — with ``FakeLlm``
supplying scripted model turns. Honest scope: the *choice* of tool calls is
scripted, so these tests verify the full plumbing, payload fidelity, error
translation and conversation behavior; whether the live model makes those
choices is measured by ``scrum-agent baseline`` (``answer_ok`` per intent).
"""

from __future__ import annotations

import asyncio

import pytest
from google.adk.models.llm_request import LlmRequest

from scrum_agent.agent.usage import UsageSnapshot
from tests.agent_fakes import FakeLlm, ScriptedStep, make_chat, tool_call
from tests.checked_queries import (
    DEFAULT_ISSUES,
    FakeJira,
    FixtureIssue,
    _with_foreign_issue,
    _with_foreign_sprint,
)
from tests.conftest import make_settings

SESSION = "test-session"


def final(text: str) -> ScriptedStep:
    return ScriptedStep(text=text)


# -- the 14 checked intents through the agent -------------------------------------
#
# (name, make_jira, steps, must_mention, must_not_mention, expected_sources)

INTENTS: list[tuple] = [
    (
        "issue-key-lookup",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("get_issue", issue_key="PAY-3"),)),
            final("PAY-3 (Statement export) is a Story in To Do, unassigned."),
        ],
        ["PAY-3", "Statement export"],
        ["PAY-1"],
        [{"issue_key": "PAY-3"}],
    ),
    (
        "issue-key-outside-pilot-project",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("get_issue", issue_key="OTHER-9"),)),
            final("Access to OTHER-9 was denied by the pilot's project scope."),
        ],
        ["denied"],
        ["Statement export", "Double charge"],
        [],
    ),
    (
        "sprint-by-id",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="78"),)),
            final("Sprint 78 is Payments R2 (active) with 3 issues."),
        ],
        ["Payments R2"],
        [],
        [{"sprint_id": 78}, {"issue_key": "PAY-1"}, {"issue_key": "PAY-2"}, {"issue_key": "PAY-3"}],
    ),
    (
        "sprint-by-exact-name",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="Payments R1"),)),
            final("Payments R1 contains PAY-5 (Login loop for SSO users)."),
        ],
        ["PAY-5", "Login loop"],
        [],
        [{"sprint_id": 77}, {"issue_key": "PAY-5"}],
    ),
    (
        "sprint-by-unique-substring",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="R3"),)),
            final("Sprint R3 (Payments R3, future) contains PAY-6."),
        ],
        ["PAY-6"],
        ["PAY-1"],
        [{"sprint_id": 79}, {"issue_key": "PAY-6"}],
    ),
    (
        "ambiguous-sprint-name",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="Payments"),)),
            final(
                "Which sprint do you mean: 77 Payments R1 [closed], "
                "78 Payments R2 [active], or 79 Payments R3 [future]?"
            ),
        ],
        ["77", "Payments R1", "78", "Payments R2", "79", "Payments R3", "?"],
        ["Double charge", "Refund webhook"],
        [],
    ),
    (
        "unknown-sprint-name",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="Onboarding"),)),
            final(
                "No sprint matching 'Onboarding' exists: the sprint was not found on this board."
            ),
        ],
        ["not found", "Onboarding"],
        ["PAY-"],
        [],
    ),
    (
        "unresolved-bugs-in-active-sprint",
        FakeJira,
        [
            ScriptedStep(
                tool_calls=(
                    tool_call(
                        "search_sprint",
                        sprint_reference="Payments R2",
                        issue_types=["Bug"],
                        unresolved_only=True,
                    ),
                )
            ),
            final(
                "There is 1 unresolved bug in Payments R2: PAY-1 (Double charge "
                "on checkout retry, In Progress, A. Developer)."
            ),
        ],
        ["PAY-1", "Double charge"],
        ["PAY-2", "PAY-5", "PAY-6"],
        [{"sprint_id": 78}, {"issue_key": "PAY-1"}],
    ),
    (
        "empty-results-are-accurate",
        FakeJira,
        [
            ScriptedStep(
                tool_calls=(
                    tool_call(
                        "search_sprint",
                        sprint_reference="Payments R2",
                        issue_types=["Epic"],
                    ),
                )
            ),
            final(
                "No issues match: there are 0 Epics in Payments R2. "
                "That is an accurate zero, not a missing result."
            ),
        ],
        ["0", "No issues match"],
        ["PAY-"],
        [{"sprint_id": 78}],
    ),
    (
        "search-returns-complete-result-set",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("search_issues", labels=["export"]),)),
            final("2 issues carry the export label: PAY-3 and PAY-6."),
        ],
        ["PAY-3", "PAY-6"],
        [],
        [{"issue_key": "PAY-3"}, {"issue_key": "PAY-6"}],
    ),
    (
        "sprint-listing-returns-complete-set",
        FakeJira,
        [
            ScriptedStep(tool_calls=(tool_call("list_sprints", states=["active"]),)),
            final("The active sprint is 78 Payments R2."),
        ],
        ["78", "Payments R2", "active"],
        ["Payments R1", "Payments R3"],
        [{"sprint_id": 78}],
    ),
    (
        "board-spanning-project-scope-fails-closed",
        _with_foreign_issue,
        [
            ScriptedStep(tool_calls=(tool_call("search_issues", labels=["payments"]),)),
            final(
                "The search was denied: results leaked outside the pilot project, "
                "so nothing from that query can be shown."
            ),
        ],
        ["denied"],
        ["PAY-1", "Double charge", "Foreign project bug"],
        [],
    ),
    (
        "foreign-origin-sprint-listed-by-board-is-in-scope",
        _with_foreign_sprint,
        [
            ScriptedStep(tool_calls=(tool_call("list_sprints"),)),
            final(
                "The board lists 4 sprints including Payments R1 (closed) and "
                "Cross-board sprint (active), which originates on another board."
            ),
        ],
        ["Payments R1", "Cross-board sprint"],
        ["denied"],
        [{"sprint_id": 77}, {"sprint_id": 78}, {"sprint_id": 79}, {"sprint_id": 81}],
    ),
    (
        "unlisted-sprint-by-id-fails-closed",
        _with_foreign_sprint,
        [
            ScriptedStep(tool_calls=(tool_call("search_sprint", sprint_reference="82"),)),
            final("Sprint 82 is not listed by the pilot board; access was denied."),
        ],
        ["denied"],
        ["Cross-board sprint"],
        [],
    ),
]


@pytest.mark.parametrize(
    "name,make_jira,steps,must_mention,must_not_mention,expected_sources",
    INTENTS,
    ids=[intent[0] for intent in INTENTS],
)
async def test_checked_intent_through_agent(
    name: str,
    make_jira,
    steps: list[ScriptedStep],
    must_mention: list[str],
    must_not_mention: list[str],
    expected_sources: list[dict],
) -> None:
    chat, _, fake = make_chat(jira=make_jira(), steps=steps)
    result = await chat.run_turn(SESSION, name)  # the intent text is the user turn
    for fragment in must_mention:
        assert fragment in result.answer, (fragment, result.answer)
    for fragment in must_not_mention:
        assert fragment not in result.answer, (fragment, result.answer)
    assert list(result.sources) == expected_sources
    assert fake._cursor == len(steps)
    await chat.aclose()


# -- follow-up questions (the Friday demo shape) ----------------------------------


async def test_blocked_follow_up_reports_accurate_zero() -> None:
    chat, _, _ = make_chat(
        steps=[
            ScriptedStep(
                tool_calls=(
                    tool_call(
                        "search_sprint",
                        sprint_reference="Payments R2",
                        issue_types=["Bug"],
                        unresolved_only=True,
                    ),
                )
            ),
            final("1 unresolved bug: PAY-1 (Double charge on checkout retry)."),
            ScriptedStep(
                tool_calls=(
                    tool_call(
                        "search_sprint",
                        sprint_reference="Payments R2",
                        statuses=["Blocked"],
                    ),
                )
            ),
            final(
                "None of them are explicitly blocked: the search for status "
                "Blocked in Payments R2 matched 0 issues. Note that blocker "
                "reasons are not available from these tools."
            ),
        ]
    )
    first = await chat.run_turn(SESSION, "Show unresolved bugs in this sprint")
    assert "PAY-1" in first.answer
    assert "PAY-2" not in first.answer

    second = await chat.run_turn(SESSION, "Which ones are explicitly blocked?")
    assert "0" in second.answer or "None" in second.answer
    assert "PAY-" not in second.answer  # no invented blocked issues
    await chat.aclose()


async def test_revoked_access_followup_never_reuses_restricted_context() -> None:
    jira = FakeJira()
    chat, _, _ = make_chat(
        jira=jira,
        steps=[
            ScriptedStep(tool_calls=(tool_call("search_issues", labels=["payments"]),)),
            final("2 payments issues: PAY-1 and PAY-2."),
            ScriptedStep(tool_calls=(tool_call("search_issues", labels=["payments"]),)),
            final(
                "That search is now denied by the pilot scope, so I cannot "
                "answer from the earlier result either."
            ),
        ],
    )
    first = await chat.run_turn(SESSION, "List the payments issues")
    assert "PAY-1" in first.answer

    # Simulate revocation: the same query now leaks a foreign issue and the
    # service fails the whole result set closed.
    jira.issues = DEFAULT_ISSUES + (
        FixtureIssue(
            key="OTHER-9",
            summary="Foreign project bug",
            status="In Progress",
            issue_type="Bug",
            labels=("payments",),
            sprint_id=78,
        ),
    )
    second = await chat.run_turn(SESSION, "List them again please")
    assert "denied" in second.answer
    assert second.sources == ()  # no earlier issue keys carried over
    await chat.aclose()


# -- usage accounting --------------------------------------------------------------


async def test_usage_accumulates_per_turn_and_cumulatively() -> None:
    chat, _, _ = make_chat(
        steps=[
            ScriptedStep(
                tool_calls=(tool_call("get_issue", issue_key="PAY-3"),),
                prompt_tokens=100,
                output_tokens=25,
            ),
            ScriptedStep(text="PAY-3 is a Story.", prompt_tokens=200, output_tokens=10),
        ]
    )
    result = await chat.run_turn(SESSION, "What is PAY-3?")
    assert result.usage == UsageSnapshot(
        model_calls=2, prompt_tokens=300, output_tokens=35, total_tokens=335
    )
    assert chat.usage.snapshot(SESSION) == result.usage
    await chat.aclose()


async def test_reset_forgets_conversation_and_usage() -> None:
    chat, _, _ = make_chat(
        steps=[
            ScriptedStep(text="First answer."),
            ScriptedStep(text="Answer after reset."),
        ]
    )
    first = await chat.run_turn(SESSION, "hello")
    assert first.turn_index == 1
    await chat.reset(SESSION)
    assert chat.usage.snapshot(SESSION) == UsageSnapshot()

    second = await chat.run_turn(SESSION, "hello again")
    assert second.turn_index == 1  # turn counter restarted
    assert second.usage.model_calls == 1
    await chat.aclose()


async def test_turn_limit_returns_notice_without_model_calls() -> None:
    chat, _, fake = make_chat(
        steps=[ScriptedStep(text="ok") for _ in range(30)]  # exactly MAX_TURNS
    )
    for index in range(30):
        await chat.run_turn(SESSION, f"turn {index}")
    over = await chat.run_turn(SESSION, "one more")
    assert over.turn_index == 31
    assert "turn limit" in over.answer
    assert over.usage == UsageSnapshot()  # no model call was spent
    assert fake._cursor == 30
    await chat.aclose()


async def test_blank_message_is_rejected() -> None:
    chat, _, _ = make_chat(steps=[])
    with pytest.raises(ValueError, match="blank"):
        await chat.run_turn(SESSION, "   ")
    await chat.aclose()


async def test_sessions_are_isolated() -> None:
    chat, _, _ = make_chat(
        steps=[
            ScriptedStep(tool_calls=(tool_call("get_issue", issue_key="PAY-3"),)),
            final("PAY-3: Statement export."),
            ScriptedStep(text="I have no prior context in this conversation."),
        ]
    )
    first = await chat.run_turn("session-a", "What is PAY-3?")
    assert "PAY-3" in first.answer
    other = await chat.run_turn("session-b", "What were we talking about?")
    assert other.usage.model_calls == 1  # independent model turn
    assert "no prior context" in other.answer
    await chat.aclose()


def test_fake_llm_is_strict_on_extra_model_calls() -> None:
    fake = FakeLlm(model="fake-checked-model", steps=[])

    async def exhaust() -> None:
        request = LlmRequest(contents=[])
        async for _ in fake.generate_content_async(request):
            pass

    with pytest.raises(AssertionError, match="exhausted"):
        asyncio.run(exhaust())


def test_sessions_stay_in_memory_without_database() -> None:
    from google.adk.sessions import InMemorySessionService

    chat, _, _ = make_chat()
    assert isinstance(chat._sessions, InMemorySessionService)
    asyncio.run(chat.aclose())


def test_sessions_persist_when_database_configured() -> None:
    from google.adk.sessions import DatabaseSessionService

    from scrum_agent.agent.chat import asyncpg_url

    settings = make_settings(database_url=" postgres://scrum_agent:pw@127.0.0.1:5432/db ")
    chat, _, _ = make_chat(settings=settings)
    assert isinstance(chat._sessions, DatabaseSessionService)
    assert asyncpg_url(settings.database_url or "") == (
        "postgresql+asyncpg://scrum_agent:pw@127.0.0.1:5432/db"
    )
    asyncio.run(chat.aclose())


async def test_aclose_awaits_database_session_close(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.adk.sessions import DatabaseSessionService

    closed: list[bool] = []

    async def fake_close(self: DatabaseSessionService) -> None:
        closed.append(True)

    monkeypatch.setattr(DatabaseSessionService, "close", fake_close)
    settings = make_settings(database_url="postgresql://scrum_agent:pw@127.0.0.1:5432/db")
    chat, _, _ = make_chat(settings=settings)
    await chat.aclose()
    assert closed == [True]
