#!/usr/bin/env python
"""One-shot split of the reusable baseline suite out of Webix into "Base".

    cd backend && uv run python scripts/split_base_workspace.py [--dry-run]

The legacy import (`scripts/import_legacy_sqlite.py`) landed the whole suite in
one workspace, but two of its five test groups are not customer work at all:

* `General Capabilities` — a general model shake-down.
* `Prompt Injection & Instruction Hierarchy` — the injection battery.

Both are the *baseline* suite every future engagement gets measured against, so
they move to a new workspace, "Base". The other three groups (`ELO Document
Intelligence`, `Rechnungsworkflow (DE)`, `Invoice Agent (Pipeline)`) are Webix's
own work and stay.

**Only root rows move.** `test_groups`, `prompts` and `toolsets` carry
`customer_id`; their children (`test_cases`, `prompt_versions`, `tools`) carry
nothing and inherit scope through their parent FK, so touching them would be
both unnecessary and wrong. Moving the two groups moves their 26 test cases,
moving the 14 `Injection *` prompts moves their 14 versions, and moving the
three mock toolsets moves their 12 tools.

**Runs cannot be split, so twelve of them are deleted.** A run is a single root
row with one `customer_id`, and its frozen `run_results` span whatever groups
the run covered — twelve of the twenty runs cover at least one moving group, so
there is no `customer_id` either workspace could honestly claim. They are
deleted (`run_results` cascade); the eight runs that touch only staying groups
are kept. **The user approved exactly this list**, and the script re-derives the
entangled set from the data and refuses if it disagrees with the approved one:
the ids are checked, not trusted.

**No endpoint moves.** Base is left with no endpoint on purpose — the user adds
one in the UI.

**Direct writes.** Like the two scripts beside it, this is a one-off data
migration and writes through the session with Core `UPDATE`/`DELETE` rather than
the repository functions: there is no "move a row between workspaces" operation
in the app and there should not be one, since the whole point of the Scope
pattern is that a row's workspace is fixed at insert. The reads still go through
the scope seams under `system_scope("split baseline suite into Base
workspace")`, which is the documented "every workspace" read. `updated_at` bumps
on the moved prompts and toolsets (SQLAlchemy's `onupdate`), which is honest:
those rows really did change.

**Safety.** Every assumption is asserted before a single write, and the script
aborts naming what disagreed. Afterwards — still inside the transaction, so a
violation rolls the whole split back — it re-checks in SQL that no reference
crosses a workspace boundary and that no test case was left unable to produce a
user message. All of it is one transaction. Re-running after a successful run is
a no-op: the applied state is recognised and reported, not applied twice.
`--dry-run` does the whole thing, prints the plan and the verification, then
rolls back.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

# `uv run python scripts/split_base_workspace.py` puts `backend/scripts` on
# sys.path rather than `backend/`, so the app package has to be pointed at.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Row, delete, func, select, text, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db import async_session, engine  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
    Endpoint,
    Prompt,
    PromptVersion,
    Run,
    RunResult,
    TestCase,
    TestCaseToolset,
    TestGroup,
    Tool,
    Toolset,
)
from app.repos.customers import create_customer  # noqa: E402
from app.repos.scoped import apply_where, scope_through_parent  # noqa: E402
from app.scope import Scope, system_scope, where_scoped  # noqa: E402

#: The workspace the suite is split out of, and the one it lands in.
SOURCE_CUSTOMER = "Webix"
TARGET_CUSTOMER = "Base"

TARGET_DESCRIPTION = (
    "The reusable baseline suite: general capabilities and the prompt-injection "
    "battery, split out of Webix so every engagement can be measured against it."
)

#: The two test groups that move, by name. Their test cases follow through
#: `group_id` and are not touched.
MOVING_GROUP_NAMES = ("General Capabilities", "Prompt Injection & Instruction Hierarchy")

#: How many test cases each moving group is expected to hold. Asserted, so a
#: group that grew or shrank since this script was written stops it.
EXPECTED_GROUP_CASES = {"General Capabilities": 11, "Prompt Injection & Instruction Hierarchy": 15}

#: The 14 `Injection *` prompts. Hard-coded because this splits one known
#: database; the guard rails below are what make the range checkable rather
#: than assumed — every one must be referenced only by cases in a moving group.
MOVING_PROMPT_IDS = range(33, 47)

#: The three mock toolsets the moving groups use. Same rule: referenced only by
#: cases in a moving group.
MOVING_TOOLSET_IDS = (4, 5, 6)

#: The runs the user approved deleting, because their results span a moving
#: group. Re-derived from the data and compared against this list.
DOOMED_RUN_IDS = (20, 21, 24, 25, 26, 27, 30, 34, 35, 36, 37, 39)

#: What the two halves of the run table must weigh. A mismatch means the data
#: moved under us and the approval no longer describes what would be deleted.
EXPECTED_DOOMED_RESULTS = 239
EXPECTED_KEPT_RUNS = 8
EXPECTED_KEPT_RESULTS = 74

#: Every reference the database cannot check for itself, as the SQL that proves
#: it. All of them must return **zero rows**: this is the whole point of the
#: split, so it is verified rather than reasoned about, and the same statements
#: are printed in the report.
CHECKS: tuple[tuple[str, str], ...] = (
    (
        "test case -> prompt (either slot) in another workspace",
        """
        select tc.id as test_case_id, tc.title, g.customer_id as case_customer,
               p.id as prompt_id, p.customer_id as prompt_customer
        from test_cases tc
        join test_groups g on g.id = tc.group_id
        join prompts p on p.id in (tc.system_prompt_id, tc.task_prompt_id)
        where p.customer_id <> g.customer_id
        """,
    ),
    (
        "test case -> toolset in another workspace",
        """
        select tct.test_case_id, g.customer_id as case_customer,
               ts.id as toolset_id, ts.customer_id as toolset_customer
        from test_case_toolsets tct
        join test_cases tc on tc.id = tct.test_case_id
        join test_groups g on g.id = tc.group_id
        join toolsets ts on ts.id = tct.toolset_id
        where ts.customer_id <> g.customer_id
        """,
    ),
    (
        "run -> endpoint in another workspace",
        """
        select r.id as run_id, r.customer_id as run_customer,
               e.id as endpoint_id, e.customer_id as endpoint_customer
        from runs r
        join endpoints e on e.id = r.endpoint_id
        where e.customer_id <> r.customer_id
        """,
    ),
    (
        "run result -> test case in another workspace",
        """
        select rr.id as run_result_id, rr.run_id, r.customer_id as run_customer,
               tc.id as test_case_id, g.customer_id as case_customer
        from run_results rr
        join runs r on r.id = rr.run_id
        join test_cases tc on tc.id = rr.test_case_id
        join test_groups g on g.id = tc.group_id
        where g.customer_id <> r.customer_id
        """,
    ),
    (
        "prompt.deployed_version_id -> version of a prompt in another workspace",
        """
        select p.id as prompt_id, p.customer_id as prompt_customer,
               pv.id as version_id, owner.customer_id as version_customer
        from prompts p
        join prompt_versions pv on pv.id = p.deployed_version_id
        join prompts owner on owner.id = pv.prompt_id
        where owner.customer_id <> p.customer_id
        """,
    ),
    (
        "prompt_versions.baseline_run_id -> run in another workspace",
        """
        select pv.id as version_id, p.customer_id as prompt_customer,
               r.id as run_id, r.customer_id as run_customer
        from prompt_versions pv
        join prompts p on p.id = pv.prompt_id
        join runs r on r.id = pv.baseline_run_id
        where r.customer_id <> p.customer_id
        """,
    ),
    (
        "test case that would send an empty user message",
        """
        select tc.id as test_case_id, tc.title
        from test_cases tc
        where coalesce(btrim(tc.content), '') = '' and tc.task_prompt_id is null
        """,
    ),
)


class SplitError(Exception):
    """The split cannot proceed, with the sentence to print."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


@dataclass
class Facts:
    """Everything the plan and the refusals are decided from, read once."""

    #: Every workspace, by name (case-folded).
    customers: dict[str, Customer]
    #: Every test group, keyed by id.
    groups: dict[int, TestGroup]
    #: Test cases per group id.
    cases_by_group: dict[int, list[TestCase]]
    #: The group a test case belongs to, keyed by case id.
    group_of_case: dict[int, int]
    #: The prompts in `MOVING_PROMPT_IDS`, keyed by id.
    movers: dict[int, Prompt]
    #: The groups each prompt id is referenced from, either slot.
    groups_using_prompt: dict[int, set[int]]
    #: The toolsets in `MOVING_TOOLSET_IDS`, keyed by id.
    toolsets: dict[int, Toolset]
    #: The groups each toolset id is referenced from.
    groups_using_toolset: dict[int, set[int]]
    #: Every run, keyed by id.
    runs: dict[int, Run]
    #: How many results each run holds.
    results_per_run: dict[int, int]
    #: The group names each run's results touch — the frozen `group_name`, plus
    #: the live group of any result still pointing at a test case.
    group_names_per_run: dict[int, set[str]]
    #: Versions whose `baseline_run_id` points at a run, keyed by run id.
    baselines_by_run: dict[int, list[int]] = field(default_factory=dict)
    #: How many prompts claim a deployed version, and how many versions claim a
    #: baseline run. Reported rather than refused on: a same-workspace pointer
    #: is perfectly legal, and `CHECKS` is what refuses a crossing one.
    deployed_pointers: int = 0
    baseline_pointers: int = 0


def _conditions(condition: object | None) -> list:
    """`where_scoped` returns `None` for "every workspace"; `.where(None)` is a
    SQLAlchemy error, so the distinction is made here — the same reason
    `apply_where` exists for the statement-level case.
    """
    return [] if condition is None else [condition]


async def read_facts(scope: Scope, session: AsyncSession) -> Facts:
    """One read per table, all through the scope seams.

    Root tables (`test_groups`, `prompts`, `toolsets`, `runs`) go through
    `where_scoped`; child tables (`test_cases`, `test_case_toolsets`,
    `run_results`, `prompt_versions`) through `scope_through_parent`. Under the
    system scope both are "every workspace", which is what this script needs:
    it is *about* the boundary between two of them.
    """
    customers = {
        row.name.casefold(): row
        for row in (await session.scalars(select(Customer).order_by(Customer.id))).all()
    }
    groups = {
        row.id: row
        for row in (
            await session.scalars(
                select(TestGroup).where(*_conditions(where_scoped(scope, TestGroup)))
            )
        ).all()
    }
    cases = list(
        (
            await session.scalars(
                apply_where(
                    select(TestCase),
                    scope_through_parent(scope, TestCase.group_id, TestGroup, TestGroup.id),
                ).order_by(TestCase.id)
            )
        ).all()
    )
    cases_by_group: dict[int, list[TestCase]] = {group_id: [] for group_id in groups}
    for case in cases:
        cases_by_group.setdefault(case.group_id, []).append(case)
    group_of_case = {case.id: case.group_id for case in cases}

    prompts = list(
        (
            await session.scalars(
                select(Prompt).where(*_conditions(where_scoped(scope, Prompt)))
            )
        ).all()
    )
    groups_using_prompt: dict[int, set[int]] = {prompt.id: set() for prompt in prompts}
    for case in cases:
        for prompt_id in (case.system_prompt_id, case.task_prompt_id):
            if prompt_id is not None:
                groups_using_prompt.setdefault(prompt_id, set()).add(case.group_id)

    toolsets = {
        row.id: row
        for row in (
            await session.scalars(
                select(Toolset).where(*_conditions(where_scoped(scope, Toolset)))
            )
        ).all()
    }
    groups_using_toolset: dict[int, set[int]] = {toolset_id: set() for toolset_id in toolsets}
    links = (
        await session.execute(
            apply_where(
                select(TestCaseToolset.test_case_id, TestCaseToolset.toolset_id),
                scope_through_parent(
                    scope, TestCaseToolset.test_case_id, TestCase, TestCase.id
                ),
            )
        )
    ).all()
    for case_id, toolset_id in links:
        groups_using_toolset.setdefault(toolset_id, set()).add(group_of_case[case_id])

    runs = {
        row.id: row
        for row in (
            await session.scalars(select(Run).where(*_conditions(where_scoped(scope, Run))))
        ).all()
    }
    results = (
        await session.execute(
            apply_where(
                select(RunResult.run_id, RunResult.group_name, RunResult.test_case_id),
                scope_through_parent(scope, RunResult.run_id, Run, Run.id),
            )
        )
    ).all()
    results_per_run: dict[int, int] = {run_id: 0 for run_id in runs}
    group_names_per_run: dict[int, set[str]] = {run_id: set() for run_id in runs}
    for run_id, group_name, case_id in results:
        results_per_run[run_id] = results_per_run.get(run_id, 0) + 1
        touched = group_names_per_run.setdefault(run_id, set())
        # The frozen name is what the run recorded; the live group catches a
        # group renamed since. Either one naming a moving group entangles it.
        touched.add(group_name)
        if case_id is not None and case_id in group_of_case:
            touched.add(groups[group_of_case[case_id]].name)

    baselines = (
        await session.execute(
            apply_where(
                select(PromptVersion.id, PromptVersion.baseline_run_id).where(
                    PromptVersion.baseline_run_id.is_not(None)
                ),
                scope_through_parent(scope, PromptVersion.prompt_id, Prompt, Prompt.id),
            )
        )
    ).all()
    baselines_by_run: dict[int, list[int]] = {}
    for version_id, run_id in baselines:
        baselines_by_run.setdefault(run_id, []).append(version_id)

    return Facts(
        customers=customers,
        groups=groups,
        cases_by_group=cases_by_group,
        group_of_case=group_of_case,
        movers={
            prompt.id: prompt for prompt in prompts if prompt.id in MOVING_PROMPT_IDS
        },
        groups_using_prompt=groups_using_prompt,
        toolsets={
            toolset_id: toolset
            for toolset_id, toolset in toolsets.items()
            if toolset_id in MOVING_TOOLSET_IDS
        },
        groups_using_toolset=groups_using_toolset,
        runs=runs,
        results_per_run=results_per_run,
        group_names_per_run=group_names_per_run,
        baselines_by_run=baselines_by_run,
        deployed_pointers=sum(
            1 for prompt in prompts if prompt.deployed_version_id is not None
        ),
        baseline_pointers=len(baselines),
    )


# ---------------------------------------------------------------------------
# The plan, and every refusal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Plan:
    """What the split will do, printed whether or not it is committed."""

    source_id: int
    group_ids: tuple[int, ...]
    prompt_ids: tuple[int, ...]
    toolset_ids: tuple[int, ...]
    run_ids: tuple[int, ...]
    doomed_results: int
    kept_runs: int
    kept_results: int
    cases_moved: int
    orphaned_baselines: tuple[int, ...]


def is_applied(facts: Facts) -> bool:
    """Whether this split has already been made in full.

    Deliberately strict, and deliberately independent of the deleted runs' ids
    being *findable*: a target workspace exists, both groups and all 14 prompts
    and all 3 toolsets sit in it, and none of the doomed runs are left. A
    partially-applied database is not "applied" and falls through to
    `build_plan`, which refuses and says what disagreed.
    """
    target = facts.customers.get(TARGET_CUSTOMER.casefold())
    if target is None:
        return False
    moving_groups = [
        group for group in facts.groups.values() if group.name in MOVING_GROUP_NAMES
    ]
    if len(moving_groups) != len(MOVING_GROUP_NAMES):
        return False
    if any(group.customer_id != target.id for group in moving_groups):
        return False
    if len(facts.movers) != len(MOVING_PROMPT_IDS) or any(
        prompt.customer_id != target.id for prompt in facts.movers.values()
    ):
        return False
    if len(facts.toolsets) != len(MOVING_TOOLSET_IDS) or any(
        toolset.customer_id != target.id for toolset in facts.toolsets.values()
    ):
        return False
    return not any(run_id in facts.runs for run_id in DOOMED_RUN_IDS)


def build_plan(facts: Facts) -> Plan:
    """The whole split, or a refusal naming exactly what disagreed."""
    source = facts.customers.get(SOURCE_CUSTOMER.casefold())
    if source is None:
        raise SplitError(
            f'No workspace named "{SOURCE_CUSTOMER}". This script splits one known '
            "database and has nothing to split."
        )
    if TARGET_CUSTOMER.casefold() in facts.customers:
        raise SplitError(
            f'A workspace named "{TARGET_CUSTOMER}" already exists but does not hold the '
            "baseline suite. This database is half split — refusing rather than guessing "
            "at the rest."
        )

    group_ids = _plan_groups(facts, source.id)
    prompt_ids = _plan_prompts(facts, source.id, group_ids)
    toolset_ids = _plan_toolsets(facts, source.id, group_ids)
    run_ids, doomed_results, kept_runs, kept_results = _plan_runs(facts, group_ids)

    orphaned = sorted(
        version_id
        for run_id in run_ids
        for version_id in facts.baselines_by_run.get(run_id, [])
    )
    return Plan(
        source_id=source.id,
        group_ids=group_ids,
        prompt_ids=prompt_ids,
        toolset_ids=toolset_ids,
        run_ids=run_ids,
        doomed_results=doomed_results,
        kept_runs=kept_runs,
        kept_results=kept_results,
        cases_moved=sum(len(facts.cases_by_group[group_id]) for group_id in group_ids),
        orphaned_baselines=tuple(orphaned),
    )


def _plan_groups(facts: Facts, source_id: int) -> tuple[int, ...]:
    """The two moving groups, each found exactly once in the source workspace."""
    group_ids: list[int] = []
    for name in MOVING_GROUP_NAMES:
        matches = [group for group in facts.groups.values() if group.name == name]
        if len(matches) != 1:
            raise SplitError(
                f'Expected exactly one test group named "{name}"; found {len(matches)}'
                + (f" ({_ids([group.id for group in matches])})" if matches else "")
                + ". The database is not what this script assumes."
            )
        group = matches[0]
        if group.customer_id != source_id:
            raise SplitError(
                f'Test group {group.id} ("{name}") is in workspace {group.customer_id}, '
                f"not {SOURCE_CUSTOMER} ({source_id})."
            )
        held = len(facts.cases_by_group[group.id])
        if held != EXPECTED_GROUP_CASES[name]:
            raise SplitError(
                f'Test group {group.id} ("{name}") holds {held} test cases, not the '
                f"{EXPECTED_GROUP_CASES[name]} this script was written against."
            )
        group_ids.append(group.id)
    return tuple(group_ids)


def _plan_prompts(
    facts: Facts, source_id: int, group_ids: tuple[int, ...]
) -> tuple[int, ...]:
    """The 14 injection prompts, proven to be used by moving groups only.

    "Used by nothing" is refused too: a prompt no test case references gives no
    evidence of which workspace it belongs to, and this script's whole standing
    to move it is that evidence.
    """
    missing = [prompt_id for prompt_id in MOVING_PROMPT_IDS if prompt_id not in facts.movers]
    if missing:
        raise SplitError(
            f"Expected prompts {MOVING_PROMPT_IDS.start}-{MOVING_PROMPT_IDS.stop - 1} to "
            f"all exist; {_ids(missing)} are absent. The id range is not what this script "
            "assumes."
        )
    elsewhere = sorted(
        prompt_id
        for prompt_id, prompt in facts.movers.items()
        if prompt.customer_id != source_id
    )
    if elsewhere:
        raise SplitError(
            f"Prompts {_ids(elsewhere)} are not in {SOURCE_CUSTOMER} ({source_id}) any "
            "more. This database is half split — refusing rather than guessing at the rest."
        )
    for prompt_id, prompt in sorted(facts.movers.items()):
        used_by = facts.groups_using_prompt.get(prompt_id, set())
        strays = sorted(used_by - set(group_ids))
        if not used_by or strays:
            raise SplitError(
                f'Prompt {prompt_id} ("{prompt.name}") is referenced by '
                + (
                    f"test cases in staying groups {_ids(strays)}"
                    if strays
                    else "no test case at all"
                )
                + ", so moving it would leave a reference crossing workspaces."
            )
    return tuple(sorted(facts.movers))


def _plan_toolsets(
    facts: Facts, source_id: int, group_ids: tuple[int, ...]
) -> tuple[int, ...]:
    """The three mock toolsets, under exactly the rule the prompts get."""
    missing = [
        toolset_id for toolset_id in MOVING_TOOLSET_IDS if toolset_id not in facts.toolsets
    ]
    if missing:
        raise SplitError(
            f"Expected toolsets {_ids(list(MOVING_TOOLSET_IDS))} to all exist; "
            f"{_ids(missing)} are absent. The ids are not what this script assumes."
        )
    elsewhere = sorted(
        toolset_id
        for toolset_id, toolset in facts.toolsets.items()
        if toolset.customer_id != source_id
    )
    if elsewhere:
        raise SplitError(
            f"Toolsets {_ids(elsewhere)} are not in {SOURCE_CUSTOMER} ({source_id}) any "
            "more. This database is half split — refusing rather than guessing at the rest."
        )
    for toolset_id, toolset in sorted(facts.toolsets.items()):
        used_by = facts.groups_using_toolset.get(toolset_id, set())
        strays = sorted(used_by - set(group_ids))
        if not used_by or strays:
            raise SplitError(
                f'Toolset {toolset_id} ("{toolset.name}") is selected by '
                + (
                    f"test cases in staying groups {_ids(strays)}"
                    if strays
                    else "no test case at all"
                )
                + ", so moving it would leave a reference crossing workspaces."
            )
    return tuple(sorted(facts.toolsets))


def _plan_runs(facts: Facts, group_ids: tuple[int, ...]) -> tuple[tuple[int, ...], int, int, int]:
    """The runs to delete — derived from the data, then held against the list
    the user approved.

    A run is entangled if any of its results names a moving group, by the frozen
    `group_name` or by the live group of a case it still points at. The approved
    ids are never trusted on their own: if the derivation disagrees with them,
    the data moved since the approval was given and the only safe outcome is to
    stop.
    """
    moving_names = {facts.groups[group_id].name for group_id in group_ids}
    entangled = tuple(
        sorted(
            run_id
            for run_id, names in facts.group_names_per_run.items()
            if names & moving_names
        )
    )
    approved = tuple(sorted(DOOMED_RUN_IDS))
    if entangled != approved:
        raise SplitError(
            f"The runs covering a moving group are {_ids(list(entangled))}, but the "
            f"approved deletion list is {_ids(list(approved))}. The data moved since "
            "that approval was given — refusing."
        )
    missing = [run_id for run_id in approved if run_id not in facts.runs]
    if missing:
        raise SplitError(f"Runs {_ids(missing)} do not exist.")

    doomed_results = sum(facts.results_per_run[run_id] for run_id in approved)
    kept = sorted(set(facts.runs) - set(approved))
    kept_results = sum(facts.results_per_run[run_id] for run_id in kept)
    if doomed_results != EXPECTED_DOOMED_RESULTS:
        raise SplitError(
            f"The {len(approved)} runs to delete hold {doomed_results} results, not the "
            f"{EXPECTED_DOOMED_RESULTS} the user approved deleting."
        )
    if len(kept) != EXPECTED_KEPT_RUNS or kept_results != EXPECTED_KEPT_RESULTS:
        raise SplitError(
            f"The runs left behind would be {len(kept)} holding {kept_results} results, "
            f"not the expected {EXPECTED_KEPT_RUNS} holding {EXPECTED_KEPT_RESULTS}."
        )
    return approved, doomed_results, len(kept), kept_results


def _ids(ids: list[int]) -> str:
    return ", ".join(str(value) for value in ids)


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


async def apply_plan(session: AsyncSession, plan: Plan) -> int:
    """Delete the entangled runs, create "Base", move the root rows into it.

    Deletion first, so the workspace is created only if the part that destroys
    something succeeded. `run_results` go with their run by `ON DELETE CASCADE`;
    a `prompt_versions.baseline_run_id` pointing at one is `SET NULL` (the plan
    reports any). All of it is the caller's single transaction.
    """
    await session.execute(delete(Run).where(Run.id.in_(plan.run_ids)))

    target = await create_customer(
        session, name=TARGET_CUSTOMER, description=TARGET_DESCRIPTION
    )
    # Root rows only: `test_cases`, `prompt_versions` and `tools` carry no
    # `customer_id` and follow their parent.
    await session.execute(
        update(TestGroup).where(TestGroup.id.in_(plan.group_ids)).values(customer_id=target.id)
    )
    await session.execute(
        update(Prompt).where(Prompt.id.in_(plan.prompt_ids)).values(customer_id=target.id)
    )
    await session.execute(
        update(Toolset).where(Toolset.id.in_(plan.toolset_ids)).values(customer_id=target.id)
    )
    return target.id


async def verify(session: AsyncSession) -> list[tuple[str, list[Row]]]:
    """Runs every check in `CHECKS` and raises if any returned a row.

    Inside the caller's transaction, so a violation rolls the whole split back
    rather than being discovered by the next page load.
    """
    findings = [
        (label, list((await session.execute(text(sql))).all())) for label, sql in CHECKS
    ]
    broken = [(label, rows) for label, rows in findings if rows]
    if broken:
        detail = "; ".join(f"{label}: {len(rows)} row(s)" for label, rows in broken)
        raise SplitError(f"Post-split verification failed — {detail}. Rolled back.")
    return findings


# ---------------------------------------------------------------------------
# Counting the end state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceCounts:
    """What one workspace holds, root tables and their children."""

    customer_id: int
    name: str
    test_groups: int
    test_cases: int
    prompts: int
    prompt_versions: int
    toolsets: int
    tools: int
    endpoints: int
    runs: int
    run_results: int

    def render(self) -> str:
        return (
            f"  {self.name} (id {self.customer_id}):  "
            f"{self.test_groups} groups, {self.test_cases} cases, "
            f"{self.prompts} prompts, {self.prompt_versions} versions, "
            f"{self.toolsets} toolsets, {self.tools} tools, "
            f"{self.endpoints} endpoints, {self.runs} runs, {self.run_results} results"
        )


async def count_workspaces(session: AsyncSession) -> list[WorkspaceCounts]:
    """The end-state table, one row per workspace, counted straight from SQL.

    Root tables count off their own `customer_id`; child tables count over the
    join to their parent, which is exactly how they inherit scope.
    """
    counts: list[WorkspaceCounts] = []
    for customer in (await session.scalars(select(Customer).order_by(Customer.id))).all():
        root = partial(_count_root, session, customer.id)
        child = partial(_count_child, session, customer.id)
        counts.append(
            WorkspaceCounts(
                customer_id=customer.id,
                name=customer.name,
                test_groups=await root(TestGroup),
                test_cases=await child(TestCase, TestCase.group_id, TestGroup),
                prompts=await root(Prompt),
                prompt_versions=await child(PromptVersion, PromptVersion.prompt_id, Prompt),
                toolsets=await root(Toolset),
                tools=await child(Tool, Tool.toolset_id, Toolset),
                endpoints=await root(Endpoint),
                runs=await root(Run),
                run_results=await child(RunResult, RunResult.run_id, Run),
            )
        )
    return counts


async def _count_root(session: AsyncSession, customer_id: int, model: type) -> int:
    statement = select(func.count()).select_from(model).where(model.customer_id == customer_id)
    return await session.scalar(statement) or 0


async def _count_child(
    session: AsyncSession, customer_id: int, model: type, foreign_key, parent: type
) -> int:
    statement = (
        select(func.count())
        .select_from(model)
        .join(parent, foreign_key == parent.id)
        .where(parent.customer_id == customer_id)
    )
    return await session.scalar(statement) or 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    """The plan, the verification and the end state — printed either way."""

    plan: Plan
    checks: list[tuple[str, list[Row]]]
    counts: list[WorkspaceCounts]
    facts: Facts

    def render(self) -> str:
        lines = [
            f"runs deleted                  {len(self.plan.run_ids)}  "
            f"({_ids(list(self.plan.run_ids))})",
            f"run_results deleted           {self.plan.doomed_results}  (cascade)",
            f"runs kept                     {self.plan.kept_runs}  "
            f"({self.plan.kept_results} results)",
            "",
            f'workspace "{TARGET_CUSTOMER}" created, moved from "{SOURCE_CUSTOMER}" '
            f"(id {self.plan.source_id}):",
            f"  test_groups                 {len(self.plan.group_ids)}  "
            f"({_ids(list(self.plan.group_ids))}) -> {self.plan.cases_moved} test cases follow",
            f"  prompts                     {len(self.plan.prompt_ids)}  "
            f"({_ids(list(self.plan.prompt_ids))}) -> their versions follow",
            f"  toolsets                    {len(self.plan.toolset_ids)}  "
            f"({_ids(list(self.plan.toolset_ids))}) -> their tools follow",
            "  endpoints                   0  (Base is left with no endpoint on purpose)",
            "",
            f"deployed_version_id set on    {self.facts.deployed_pointers} prompts",
            f"baseline_run_id set on        {self.facts.baseline_pointers} versions",
            "baselines orphaned by the delete   "
            + (
                _ids(list(self.plan.orphaned_baselines))
                if self.plan.orphaned_baselines
                else "none"
            ),
            "",
            "cross-workspace verification (every one must be empty):",
        ]
        lines += [
            f"  {len(rows):>3} row(s)  {label}" for label, rows in self.checks
        ]
        lines += ["", "end state:"]
        lines += [count.render() for count in self.counts]
        return "\n".join(lines)


class _DryRun(Exception):
    """Carries the report out through the transaction block, which rolls back on
    the way — a rollback by exception rather than by a flag nobody can forget to
    honour. Same device as the two scripts beside this one.
    """

    def __init__(self, report: Report) -> None:
        super().__init__("dry run")
        self.report = report


async def run(dry_run: bool) -> Report | None:
    """The report of what was applied (or planned), or `None` if already applied."""
    scope = system_scope("split baseline suite into Base workspace")
    try:
        async with async_session() as session:
            try:
                async with session.begin():
                    facts = await read_facts(scope, session)
                    if is_applied(facts):
                        return None
                    plan = build_plan(facts)
                    await apply_plan(session, plan)
                    session.expire_all()
                    checks = await verify(session)
                    report = Report(
                        plan=plan,
                        checks=checks,
                        counts=await count_workspaces(session),
                        facts=facts,
                    )
                    if dry_run:
                        raise _DryRun(report)
            except _DryRun as rolled_back:
                return rolled_back.report
            return report
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Split the reusable baseline suite (General Capabilities, Prompt Injection & "
            'Instruction Hierarchy) out of Webix into a new "Base" workspace.'
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do the whole split, print the plan and the verification, then roll back",
    )
    args = parser.parse_args()

    try:
        report = asyncio.run(run(args.dry_run))
    except SplitError as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 1

    if report is None:
        print(
            f'Already applied: workspace "{TARGET_CUSTOMER}" holds both baseline groups, '
            f"prompts {MOVING_PROMPT_IDS.start}-{MOVING_PROMPT_IDS.stop - 1} and toolsets "
            f"{_ids(list(MOVING_TOOLSET_IDS))}, and the entangled runs are gone. "
            "Nothing to do."
        )
        return 0

    print(report.render())
    print()
    print("Rolled back (--dry-run)." if args.dry_run else "Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
