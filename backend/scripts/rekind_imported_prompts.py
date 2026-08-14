#!/usr/bin/env python
"""One-shot correction of the legacy import: per-case prompts move to `task`.

    cd backend && uv run python scripts/rekind_imported_prompts.py [--dry-run]

`scripts/import_legacy_sqlite.py` landed every prompt as `kind = "system"`,
because the old app had no task-prompt channel at all and inventing one during
the import would have been a guess about what went on the wire. Four of the
resulting prompts (ids 25-28, the "WEDI Base Prompts ..." rows) really were
shared system prompts and stay as they are. The other 18 (ids 29-46) each came
from one test case's inline `custom_system_text` — an instruction for that one
call, which is what the task channel is for — so each of them is re-kinded and
its owning test case's reference moves from the system slot to the task slot.

Order matters and is the reason this is a script rather than a migration. The
app refuses a kind change while a test case still references the prompt
(`PromptKindChangeError`), and `assert_prompt_slot` refuses a reference to a
prompt of the wrong kind, so the three writes run as: clear the system slot,
flip the kind, set the task slot. No intermediate state is one the app's own
rules would reject, and all three are **one transaction** — a half-applied
re-kind would leave a test case pointing at nothing.

**Direct writes.** Like the importer beside it, this is a one-off data
migration and writes through the session with Core `UPDATE`s rather than the
repository functions: those exist to enforce the very rules this correction has
to step around (each of the three statements above is individually a state the
repositories refuse), and weakening them for a one-shot script would be the
wrong trade. The reads still go through the scope seams under
`system_scope("re-kind imported prompts")`, which is the documented "every
workspace" read. `updated_at` bumps on every touched row, which is honest: the
rows really did change.

**Not touched.** `run_results.system_prompt_version_id` keeps its 57 attributed
rows, and the frozen snapshot texts keep their values. Those runs really did
send that text as the *system* message; moving the attribution to
`task_prompt_version_id` would make the history lie about what was on the wire.
Only future runs are affected. Nothing is merged or deduped either — ids 30, 31
and 46 hold byte-identical content, and consolidating them is a separate
decision.

**Safety.** Every assumption above is asserted before a single write, and the
script aborts naming what disagreed: if the id range is not what we think it
is, doing nothing is the only safe outcome. Re-running after a successful run
is a no-op — the applied state is recognised and reported, not applied twice.
`--dry-run` does the whole thing and rolls back.
"""

import argparse
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

# `uv run python scripts/rekind_imported_prompts.py` puts `backend/scripts` on
# sys.path rather than `backend/`, so the app package has to be pointed at.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db import async_session, engine  # noqa: E402
from app.models import Prompt, TestCase, TestGroup  # noqa: E402
from app.repos.scoped import apply_where, scope_through_parent  # noqa: E402
from app.scope import Scope, system_scope, where_scoped  # noqa: E402
from app.services.message_assembly import user_message  # noqa: E402

#: The imported prompts that came from one test case's inline text, and so
#: belong on the task channel. Hard-coded because this corrects one known
#: import: the guard rails below are what make the range checkable rather than
#: assumed.
RE_KIND_IDS = range(29, 47)

#: The genuinely shared system prompts, which stay `system`. Read only to prove
#: the id range is the one this script was written against.
KEEP_SYSTEM_IDS = range(25, 29)


class ReKindError(Exception):
    """The correction cannot proceed, with the sentence to print."""


@dataclass(frozen=True)
class Move:
    """One prompt and the single test case whose slot it moves between."""

    prompt_id: int
    prompt_name: str
    test_case_id: int
    test_case_title: str

    def render(self) -> str:
        return (
            f"  prompt {self.prompt_id:>3}  system -> task   "
            f'test case {self.test_case_id:>3}  "{self.prompt_name}"'
        )


@dataclass
class Facts:
    """Everything the plan and the refusals are decided from, read once."""

    #: The prompts found in `RE_KIND_IDS`, keyed by id.
    movers: dict[int, Prompt]
    #: The prompts found in `KEEP_SYSTEM_IDS`, keyed by id.
    keepers: dict[int, Prompt]
    #: Every test case in scope.
    cases: list[TestCase]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def read_facts(scope: Scope, session: AsyncSession) -> Facts:
    """The prompts in both id ranges and every test case, in three reads.

    Test cases are scoped through their group, the way every child table is:
    under the system scope that predicate is `None`, i.e. all workspaces.
    """
    movers = await _read_prompts(scope, session, RE_KIND_IDS)
    keepers = await _read_prompts(scope, session, KEEP_SYSTEM_IDS)
    cases = (
        (
            await session.execute(
                apply_where(
                    select(TestCase),
                    scope_through_parent(scope, TestCase.group_id, TestGroup, TestGroup.id),
                ).order_by(TestCase.id)
            )
        )
        .scalars()
        .all()
    )
    return Facts(movers=movers, keepers=keepers, cases=list(cases))


async def _read_prompts(
    scope: Scope, session: AsyncSession, ids: range
) -> dict[int, Prompt]:
    rows = (
        (
            await session.execute(
                select(Prompt).where(
                    *_conditions(where_scoped(scope, Prompt, Prompt.id.in_(ids)))
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


def _conditions(condition: object | None) -> list:
    """`where_scoped` returns `None` for "every workspace"; `.where(None)` is a
    SQLAlchemy error, so the distinction is made here — the same reason
    `apply_where` exists for the statement-level case.
    """
    return [] if condition is None else [condition]


# ---------------------------------------------------------------------------
# The plan, and every refusal
# ---------------------------------------------------------------------------


def is_applied(facts: Facts) -> bool:
    """Whether this correction has already been made in full.

    Deliberately strict: every one of the 18 prompts is `task`, and every one is
    referenced by exactly one test case in the **task** slot and by none in the
    system slot. A partially-applied database is not "applied" and falls through
    to `build_plan`, which will refuse and say what disagreed.
    """
    if len(facts.movers) != len(RE_KIND_IDS):
        return False
    if any(prompt.kind != "task" for prompt in facts.movers.values()):
        return False
    for prompt_id in facts.movers:
        in_task = [case for case in facts.cases if case.task_prompt_id == prompt_id]
        in_system = [case for case in facts.cases if case.system_prompt_id == prompt_id]
        if len(in_task) != 1 or in_system:
            return False
    return True


def build_plan(facts: Facts) -> list[Move]:
    """The 18 moves, or a refusal naming exactly what disagreed."""
    missing = [prompt_id for prompt_id in RE_KIND_IDS if prompt_id not in facts.movers]
    if missing:
        raise ReKindError(
            f"Expected prompts {RE_KIND_IDS.start}-{RE_KIND_IDS.stop - 1} to all exist; "
            f"{_ids(missing)} are absent. The id range is not what this script assumes."
        )
    not_system = sorted(
        prompt_id for prompt_id, prompt in facts.movers.items() if prompt.kind != "system"
    )
    if not_system:
        raise ReKindError(
            f"Prompts {_ids(not_system)} are not kind='system' any more, but the rest are. "
            "This database is half re-kinded — refusing rather than guessing at the rest."
        )

    missing_keepers = [
        prompt_id for prompt_id in KEEP_SYSTEM_IDS if prompt_id not in facts.keepers
    ]
    not_kept_system = sorted(
        prompt_id for prompt_id, prompt in facts.keepers.items() if prompt.kind != "system"
    )
    if missing_keepers or not_kept_system:
        raise ReKindError(
            f"Prompts {KEEP_SYSTEM_IDS.start}-{KEEP_SYSTEM_IDS.stop - 1} should be four "
            "existing kind='system' prompts (the shared WEDI base prompts), but "
            + (f"{_ids(missing_keepers)} are absent" if missing_keepers else "")
            + (" and " if missing_keepers and not_kept_system else "")
            + (f"{_ids(not_kept_system)} are not system" if not_kept_system else "")
            + ". The id range is not what this script assumes."
        )

    task_slot_used = [case.id for case in facts.cases if case.task_prompt_id is not None]
    if task_slot_used:
        raise ReKindError(
            f"Test cases {_ids(task_slot_used)} already use the task slot. This script "
            "assumes the task channel is entirely unused, and will not overwrite a "
            "reference somebody made deliberately."
        )

    moves: list[Move] = []
    for prompt_id, prompt in sorted(facts.movers.items()):
        referencing = [
            case for case in facts.cases if case.system_prompt_id == prompt_id
        ]
        if len(referencing) != 1:
            raise ReKindError(
                f'Prompt {prompt_id} ("{prompt.name}") is referenced by '
                f"{len(referencing)} test cases in the system slot, not exactly one"
                + (f" ({_ids([case.id for case in referencing])})" if referencing else "")
                + ". These prompts are supposed to be one-per-case imports."
            )
        case = referencing[0]
        if prompt.name != case.title:
            raise ReKindError(
                f'Prompt {prompt_id} is named "{prompt.name}" but its only test case '
                f'({case.id}) is titled "{case.title}". The importer names a per-case '
                "prompt after its case, so this id range is not what we think it is."
            )
        moves.append(
            Move(
                prompt_id=prompt_id,
                prompt_name=prompt.name,
                test_case_id=case.id,
                test_case_title=case.title,
            )
        )
    return moves


def _ids(ids: list[int]) -> str:
    return ", ".join(str(value) for value in ids)


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------


async def apply_moves(session: AsyncSession, moves: list[Move]) -> None:
    """Clear the system slot, flip the kind, then set the task slot.

    Three steps in that order so that at no point does a test case reference a
    prompt whose kind does not match the slot it sits in — the state
    `assert_prompt_slot` exists to refuse. All of it is the caller's single
    transaction.
    """
    case_ids = [move.test_case_id for move in moves]
    prompt_ids = [move.prompt_id for move in moves]

    await session.execute(
        update(TestCase).where(TestCase.id.in_(case_ids)).values(system_prompt_id=None)
    )
    await session.execute(
        update(Prompt).where(Prompt.id.in_(prompt_ids)).values(kind="task")
    )
    for move in moves:
        await session.execute(
            update(TestCase)
            .where(TestCase.id == move.test_case_id)
            .values(task_prompt_id=move.prompt_id)
        )


async def assert_no_empty_user_message(scope: Scope, session: AsyncSession) -> None:
    """Re-reads every test case and checks each still sends *something*.

    The app's own rule (`app.services.message_assembly.assert_user_message`) is
    that a case whose task prompt and content are both blank would send an empty
    user message, and it is checked at authoring time and again at run creation.
    A bulk `UPDATE` goes around both, so it is checked here — inside the
    transaction, so a violation rolls the whole correction back rather than
    being discovered by the next run.
    """
    facts = await read_facts(scope, session)
    texts = dict(
        (
            await session.execute(
                select(Prompt.id, Prompt.content).where(
                    *_conditions(where_scoped(scope, Prompt))
                )
            )
        ).all()
    )
    empty = [
        case.id
        for case in facts.cases
        if not user_message(
            None if case.task_prompt_id is None else texts[case.task_prompt_id],
            case.content,
        )
    ]
    if empty:
        raise ReKindError(
            f"After the change, test cases {_ids(empty)} would send an empty user "
            "message. Rolled back."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class _DryRun(Exception):
    """Carries the plan out through the transaction block, which rolls back on
    the way — a rollback by exception rather than by a flag nobody can forget to
    honour. Same device as the importer's.
    """

    def __init__(self, moves: list[Move]) -> None:
        super().__init__("dry run")
        self.moves = moves


async def run(dry_run: bool) -> list[Move] | None:
    """The moves that were applied (or planned), or `None` if already applied."""
    scope = system_scope("re-kind imported prompts")
    try:
        async with async_session() as session:
            try:
                async with session.begin():
                    facts = await read_facts(scope, session)
                    if is_applied(facts):
                        return None
                    moves = build_plan(facts)
                    await apply_moves(session, moves)
                    session.expire_all()
                    await assert_no_empty_user_message(scope, session)
                    if dry_run:
                        raise _DryRun(moves)
            except _DryRun as rolled_back:
                return rolled_back.moves
            return moves
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Move the imported per-case prompts from the system channel to the task "
            "channel, and their test cases' references with them."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do the whole change, print the plan, then roll back",
    )
    args = parser.parse_args()

    try:
        moves = asyncio.run(run(args.dry_run))
    except ReKindError as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 1

    if moves is None:
        print(
            f"Already applied: prompts {RE_KIND_IDS.start}-{RE_KIND_IDS.stop - 1} are "
            "kind='task' and each is referenced by exactly one test case's task slot. "
            "Nothing to do."
        )
        return 0

    print(f"prompts re-kinded system -> task   {len(moves)}")
    print(f"test case slots moved              {len(moves)}")
    print()
    for move in moves:
        print(move.render())
    print()
    print("Rolled back (--dry-run)." if args.dry_run else "Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
