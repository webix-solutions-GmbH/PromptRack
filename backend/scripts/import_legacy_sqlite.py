#!/usr/bin/env python
"""One-shot import of the retired modelfit SQLite database into PromptRack.

    cd backend && uv run python scripts/import_legacy_sqlite.py \\
        --sqlite ../docs/app.db --customer Webix [--dry-run]

The old app (tagged `legacy-nextjs`: Next.js/Drizzle/SQLite) and this one differ by the whole
domain-model pivot, so this is a *translation*, not a copy:

* `system_prompts` -> `prompts` (the versioned asset), one row each.
* A test case's inline `custom_system_text` (`system_prompt_mode = 'override'`)
  -> **its own** `prompts` row, named after the case that owned it. That text
  was unversioned in the old app, and the pivot exists precisely to make it a
  named asset with a history.
* Every imported prompt gets **v1** committed through the real versioning path
  (`app.repos.prompt_versions.commit_version`), so its history is
  indistinguishable from a commit made in the UI. No `deployed_version_id` and
  no `baseline_run_id` are set — both are human claims this script has no
  standing to make.
* `prompt_groups` -> `test_groups`, `prompts` -> `test_cases`,
  `prompt_toolsets` -> `test_case_toolsets`.
* `run_results.prompt_text` -> `test_case_text` and `system_prompt_text` ->
  `system_prompt_text`; `task_prompt_text` is NULL throughout, because the old
  app had no task-prompt channel at all.
* `run_results.system_prompt_version_id` is **attribution, not selection**: it
  is set only where the frozen `system_prompt_text` is byte-identical to the v1
  this script just committed for the prompt that result's test case resolves
  to, decided by `app.services.attribution.match_version` — the same rule run
  creation uses. Everything else stays NULL rather than being guessed at; a run
  of a since-edited prompt genuinely has no version standing behind it.

Old integer ids are not preserved. Every reference is remapped through the
in-memory maps built as each table is written.

**Scope.** This is an operational tool, so it constructs its own
`system_scope("legacy sqlite import")` and uses it for the repository calls that
take one (`commit_version`, `insert_run_results`). The root rows — endpoints,
prompts, toolsets, test groups, runs — are inserted **directly through the
session** with an explicit `customer_id`, because `scope_values()` deliberately
refuses a system scope (a new row has no defensible workspace to land in) and
weakening that guard for a one-shot script would be the wrong trade. The
workspace those inserts land in is the one resolved once at the top of
`import_legacy`, and nothing here writes outside it.

**Safety.** The whole import is one transaction. It refuses outright if the
target workspace already holds endpoints, prompts, toolsets, test groups or runs:
this is a one-shot import, not a sync, and there are no upsert semantics to fall
back on. `--dry-run` does the entire translation and rolls back.

The legacy database is opened **read-only** (`mode=ro`) and never written to.
"""

import argparse
import asyncio
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `uv run python scripts/import_legacy_sqlite.py` puts `backend/scripts` on
# sys.path rather than `backend/`, so the app package has to be pointed at.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db import async_session, engine  # noqa: E402
from app.models import (  # noqa: E402
    Endpoint,
    EndpointModel,
    Prompt,
    Run,
    TestCase,
    TestCaseToolset,
    TestGroup,
    Tool,
    Toolset,
)
from app.repos.customers import (  # noqa: E402
    count_customer_content,
    create_customer,
    find_customer_by_name,
)
from app.repos.prompt_versions import commit_version  # noqa: E402
from app.repos.runs import insert_run_results  # noqa: E402
from app.scope import Scope, system_scope  # noqa: E402
from app.services.attribution import VersionRef, match_version  # noqa: E402

#: The message every imported prompt's v1 carries. One sentence, so the history
#: is honest about where the text came from rather than pretending it was
#: authored here.
COMMIT_MESSAGE = "Imported from modelfit"


class LegacyImportError(Exception):
    """The import cannot proceed, with the sentence to print."""


# ---------------------------------------------------------------------------
# Legacy reading
# ---------------------------------------------------------------------------


def open_legacy(path: Path) -> sqlite3.Connection:
    """Opens the old database read-only, so a mistake here cannot touch it."""
    if not path.exists():
        raise LegacyImportError(f"No SQLite database at {path}.")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def read_table(connection: sqlite3.Connection, table: str, order: str) -> list[sqlite3.Row]:
    return connection.execute(f'select * from "{table}" order by {order}').fetchall()


def to_utc(epoch_ms: int | None) -> datetime | None:
    """Legacy timestamps are epoch **milliseconds**; every column here is
    `timestamptz`, so a naive datetime would be read in the process's local zone.
    """
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, UTC)


def to_utc_required(epoch_ms: int) -> datetime:
    stamp = to_utc(epoch_ms)
    assert stamp is not None
    return stamp


# ---------------------------------------------------------------------------
# The prompt rule
# ---------------------------------------------------------------------------

#: How a legacy test case's system channel resolves: at a shared
#: `system_prompts` row, at its own inline text, or at nothing.
type SystemSource = tuple[str, Any]


def classify_system_prompt(case: sqlite3.Row) -> SystemSource:
    """Which system prompt a legacy test case ran with.

    The old schema allowed three shapes and the data only ever uses three, but
    it could not *express* that constraint, so this asserts it: a row carrying
    both a `system_prompt_id` and inline text would be an ambiguity no mapping
    can resolve, and the import stops rather than picking one.
    """
    mode = case["system_prompt_mode"]
    reference = case["system_prompt_id"]
    inline = case["custom_system_text"]
    has_inline = bool((inline or "").strip())

    if mode not in ("append", "override"):
        raise LegacyImportError(
            f'Test case {case["id"]} ("{case["title"]}") has an unknown '
            f"system_prompt_mode {mode!r}."
        )
    if reference is not None and has_inline:
        raise LegacyImportError(
            f'Test case {case["id"]} ("{case["title"]}") has both a system_prompt_id '
            "and custom_system_text — the import has no way to know which one was sent."
        )
    if mode == "override":
        if not has_inline:
            raise LegacyImportError(
                f'Test case {case["id"]} ("{case["title"]}") is in override mode but '
                "has no custom_system_text."
            )
        return ("inline", inline)
    if has_inline:
        raise LegacyImportError(
            f'Test case {case["id"]} ("{case["title"]}") is in append mode but carries '
            "custom_system_text, which the old app never sent."
        )
    if reference is not None:
        return ("reference", reference)
    return ("none", None)


class NameAllocator:
    """Hands out prompt names that are unique within the workspace.

    Two test cases with the same title would otherwise produce two prompts that
    are impossible to tell apart in the picker — and MCP resolves prompts by
    name, where an ambiguous one is refused rather than guessed.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def take(self, name: str) -> str:
        candidate = name
        suffix = 1
        while candidate.casefold() in self._taken:
            suffix += 1
            candidate = f"{name} ({suffix})"
        self._taken.add(candidate.casefold())
        return candidate


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


@dataclass
class Summary:
    """What the import wrote, printed whether or not it was committed."""

    customer: str
    customer_created: bool = False
    endpoints: int = 0
    endpoint_models: int = 0
    toolsets: int = 0
    tools: int = 0
    prompts_from_system_prompts: int = 0
    prompts_from_inline_text: int = 0
    prompt_versions: int = 0
    test_groups: int = 0
    test_cases: int = 0
    test_cases_without_system_prompt: int = 0
    test_case_toolsets: int = 0
    runs: int = 0
    run_results: int = 0
    attributed: int = 0
    unattributed_no_system_prompt: int = 0
    unattributed_text_differs: int = 0

    @property
    def prompts(self) -> int:
        return self.prompts_from_system_prompts + self.prompts_from_inline_text

    def render(self) -> str:
        lines = [
            f"workspace                     {self.customer}"
            f"{' (created)' if self.customer_created else ' (existing)'}",
            f"endpoints                     {self.endpoints}",
            f"endpoint_models               {self.endpoint_models}",
            f"toolsets                      {self.toolsets}",
            f"tools                         {self.tools}",
            f"prompts                       {self.prompts}"
            f"  ({self.prompts_from_system_prompts} from system_prompts, "
            f"{self.prompts_from_inline_text} from inline custom_system_text)",
            f"prompt_versions               {self.prompt_versions}  (v1 each)",
            f"test_groups                   {self.test_groups}",
            f"test_cases                    {self.test_cases}"
            f"  ({self.test_cases_without_system_prompt} with no system prompt)",
            f"test_case_toolsets            {self.test_case_toolsets}",
            f"runs                          {self.runs}",
            f"run_results                   {self.run_results}",
            "",
            "version attribution (system slot):",
            f"  attributed to a v1          {self.attributed}",
            f"  no system prompt frozen     {self.unattributed_no_system_prompt}",
            f"  text differs from v1        {self.unattributed_text_differs}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


async def resolve_workspace(
    session: AsyncSession, name: str, summary: Summary
) -> int:
    """The target workspace, created if absent — and refused if not empty.

    The emptiness check is over exactly the five root tables `ON DELETE
    RESTRICT` guards, which is the same list `delete_customer` refuses on: if
    any of them holds a row, a second import would silently duplicate the whole
    suite, and there is no merge semantics to fall back on.
    """
    customer = await find_customer_by_name(session, name)
    if customer is None:
        customer = await create_customer(
            session, name=name, description="Imported from the modelfit SQLite database."
        )
        summary.customer_created = True
        return customer.id

    counts = await count_customer_content(session, customer.id)
    if counts.total:
        held = ", ".join(
            f"{count} {label}"
            for label, count in (
                ("endpoints", counts.endpoints),
                ("prompts", counts.prompts),
                ("toolsets", counts.toolsets),
                ("test groups", counts.test_groups),
                ("runs", counts.runs),
            )
            if count
        )
        raise LegacyImportError(
            f'Workspace "{customer.name}" (id {customer.id}) already holds {held}. '
            "This is a one-shot import, not a sync — refusing rather than duplicating. "
            "Empty the workspace, or import into a new one with --customer."
        )
    return customer.id


async def import_legacy(
    session: AsyncSession,
    connection: sqlite3.Connection,
    customer_name: str,
) -> Summary:
    """Translates the whole legacy database into one workspace."""
    summary = Summary(customer=customer_name)
    scope = system_scope("legacy sqlite import")
    customer_id = await resolve_workspace(session, customer_name, summary)

    endpoint_ids = await _import_endpoints(session, connection, customer_id, summary)
    await _import_endpoint_models(session, connection, endpoint_ids, summary)
    toolset_ids = await _import_toolsets(session, connection, customer_id, summary)
    await _import_tools(session, connection, toolset_ids, summary)

    cases = read_table(connection, "prompts", "id")
    prompt_for_case, versions = await _import_prompts(
        scope, session, connection, cases, customer_id, summary
    )

    group_ids = await _import_test_groups(session, connection, customer_id, summary)
    case_ids = await _import_test_cases(
        session, cases, group_ids, prompt_for_case, summary
    )
    await _import_case_toolsets(session, connection, case_ids, toolset_ids, summary)
    await _import_runs(
        scope,
        session,
        connection,
        customer_id,
        endpoint_ids,
        case_ids,
        prompt_for_case,
        versions,
        summary,
    )
    return summary


async def _import_endpoints(
    session: AsyncSession,
    connection: sqlite3.Connection,
    customer_id: int,
    summary: Summary,
) -> dict[int, int]:
    """`machines` there, `endpoints` here.

    Every `"machine…"` string below names a *source* table or column: the old
    app's SQLite file predates the rename and will never be migrated, so the
    read side keeps saying machine while everything written says endpoint.
    """
    ids: dict[int, int] = {}
    for row in read_table(connection, "machines", "id"):
        endpoint = Endpoint(
            customer_id=customer_id,
            name=row["name"],
            base_url=row["base_url"],
            api_key=row["api_key"],
            cpu=row["cpu"],
            ram=row["ram"],
            gpu=row["gpu"],
            notes=row["notes"],
            created_at=to_utc_required(row["created_at"]),
            updated_at=to_utc_required(row["updated_at"]),
        )
        session.add(endpoint)
        await session.flush()
        ids[row["id"]] = endpoint.id
    summary.endpoints = len(ids)
    return ids


async def _import_endpoint_models(
    session: AsyncSession,
    connection: sqlite3.Connection,
    endpoint_ids: dict[int, int],
    summary: Summary,
) -> None:
    for row in read_table(connection, "machine_models", "id"):
        session.add(
            EndpointModel(
                endpoint_id=endpoint_ids[row["machine_id"]],
                model_id=row["model_id"],
                currently_loaded=bool(row["currently_loaded"]),
                first_seen_at=to_utc_required(row["first_seen_at"]),
                last_seen_at=to_utc_required(row["last_seen_at"]),
                source=row["source"],
            )
        )
        summary.endpoint_models += 1
    await session.flush()


async def _import_toolsets(
    session: AsyncSession,
    connection: sqlite3.Connection,
    customer_id: int,
    summary: Summary,
) -> dict[int, int]:
    ids: dict[int, int] = {}
    for row in read_table(connection, "toolsets", "id"):
        toolset = Toolset(
            customer_id=customer_id,
            name=row["name"],
            description=row["description"],
            kind=row["kind"],
            mcp_url=row["mcp_url"],
            mcp_headers=row["mcp_headers"],
            created_at=to_utc_required(row["created_at"]),
            updated_at=to_utc_required(row["updated_at"]),
        )
        session.add(toolset)
        await session.flush()
        ids[row["id"]] = toolset.id
    summary.toolsets = len(ids)
    return ids


async def _import_tools(
    session: AsyncSession,
    connection: sqlite3.Connection,
    toolset_ids: dict[int, int],
    summary: Summary,
) -> None:
    for row in read_table(connection, "tools", "id"):
        session.add(
            Tool(
                toolset_id=toolset_ids[row["toolset_id"]],
                name=row["name"],
                description=row["description"],
                parameters_json=row["parameters_json"],
                mock_response=row["mock_response"],
                enabled=bool(row["enabled"]),
                source=row["source"],
                first_seen_at=to_utc_required(row["first_seen_at"]),
                last_seen_at=to_utc_required(row["last_seen_at"]),
            )
        )
        summary.tools += 1
    await session.flush()


async def _import_prompts(
    scope: Scope,
    session: AsyncSession,
    connection: sqlite3.Connection,
    cases: Sequence[sqlite3.Row],
    customer_id: int,
    summary: Summary,
) -> tuple[dict[int, int], dict[int, VersionRef]]:
    """Writes every prompt asset and commits v1 of each.

    Two sources feed one table: the four shared `system_prompts`, and the inline
    `custom_system_text` of every override case. The second is the pivot's whole
    point — that text had no name, no history and no diff, and now it has all
    three.

    Returns the map from a legacy **test case** id to the new prompt id its
    system slot points at (absent = the case had no system prompt), and the v1
    of every new prompt keyed by prompt id, which is what run attribution
    compares frozen text against.
    """
    names = NameAllocator()
    prompt_for_legacy_system: dict[int, int] = {}
    prompt_for_case: dict[int, int] = {}

    for row in read_table(connection, "system_prompts", "id"):
        prompt = Prompt(
            customer_id=customer_id,
            name=names.take(row["name"]),
            kind="system",
            content=row["content"],
            created_at=to_utc_required(row["created_at"]),
            updated_at=to_utc_required(row["updated_at"]),
        )
        session.add(prompt)
        await session.flush()
        prompt_for_legacy_system[row["id"]] = prompt.id
        summary.prompts_from_system_prompts += 1

    for case in cases:
        source, value = classify_system_prompt(case)
        if source == "reference":
            prompt_for_case[case["id"]] = prompt_for_legacy_system[value]
            continue
        if source == "none":
            summary.test_cases_without_system_prompt += 1
            continue
        prompt = Prompt(
            customer_id=customer_id,
            name=names.take(case["title"]),
            kind="system",
            content=value,
            created_at=to_utc_required(case["created_at"]),
            updated_at=to_utc_required(case["updated_at"]),
        )
        session.add(prompt)
        await session.flush()
        prompt_for_case[case["id"]] = prompt.id
        summary.prompts_from_inline_text += 1

    # Through the real commit path, so the rows are indistinguishable from a
    # commit made in the UI (sequential `version`, the head check, the
    # transaction it computes `max + 1` inside). `user_id` stays None: no human
    # made this commit.
    versions: dict[int, VersionRef] = {}
    for prompt_id in sorted(
        set(prompt_for_legacy_system.values()) | set(prompt_for_case.values())
    ):
        version = await commit_version(
            scope, session, prompt_id, message=COMMIT_MESSAGE
        )
        versions[prompt_id] = VersionRef(
            id=version.id, version=version.version, content=version.content
        )
        summary.prompt_versions += 1

    return prompt_for_case, versions


async def _import_test_groups(
    session: AsyncSession,
    connection: sqlite3.Connection,
    customer_id: int,
    summary: Summary,
) -> dict[int, int]:
    ids: dict[int, int] = {}
    for row in read_table(connection, "prompt_groups", "id"):
        group = TestGroup(
            customer_id=customer_id,
            name=row["name"],
            description=row["description"],
            sort_order=row["sort_order"],
            created_at=to_utc_required(row["created_at"]),
        )
        session.add(group)
        await session.flush()
        ids[row["id"]] = group.id
    summary.test_groups = len(ids)
    return ids


async def _import_test_cases(
    session: AsyncSession,
    cases: Sequence[sqlite3.Row],
    group_ids: dict[int, int],
    prompt_for_case: dict[int, int],
    summary: Summary,
) -> dict[int, int]:
    """`task_prompt_id` is NULL for every case: the old app had no such channel,
    and inventing one would move text between the system and user messages.
    """
    ids: dict[int, int] = {}
    for row in cases:
        case = TestCase(
            group_id=group_ids[row["group_id"]],
            title=row["title"],
            content=row["content"],
            expected_output=row["expected_output"],
            system_prompt_id=prompt_for_case.get(row["id"]),
            task_prompt_id=None,
            tool_mode=row["tool_mode"],
            tool_choice=row["tool_choice"],
            max_turns=row["max_turns"],
            sort_order=row["sort_order"],
            created_at=to_utc_required(row["created_at"]),
            updated_at=to_utc_required(row["updated_at"]),
        )
        session.add(case)
        await session.flush()
        ids[row["id"]] = case.id
    summary.test_cases = len(ids)
    return ids


async def _import_case_toolsets(
    session: AsyncSession,
    connection: sqlite3.Connection,
    case_ids: dict[int, int],
    toolset_ids: dict[int, int],
    summary: Summary,
) -> None:
    for row in read_table(connection, "prompt_toolsets", "prompt_id, toolset_id"):
        session.add(
            TestCaseToolset(
                test_case_id=case_ids[row["prompt_id"]],
                toolset_id=toolset_ids[row["toolset_id"]],
                sort_order=row["sort_order"],
            )
        )
        summary.test_case_toolsets += 1
    await session.flush()


async def _import_runs(
    scope: Scope,
    session: AsyncSession,
    connection: sqlite3.Connection,
    customer_id: int,
    endpoint_ids: dict[int, int],
    case_ids: dict[int, int],
    prompt_for_case: dict[int, int],
    versions: dict[int, VersionRef],
    summary: Summary,
) -> None:
    """Runs and their frozen result rows, one multi-row INSERT per run.

    The three-text split is where the old single `prompt_text` lands: the case's
    own data goes to `test_case_text`, the frozen system prompt keeps its
    column, and `task_prompt_text` stays NULL.
    """
    results_by_run: dict[int, list[sqlite3.Row]] = {}
    for row in read_table(connection, "run_results", "run_id, sort_order, id"):
        results_by_run.setdefault(row["run_id"], []).append(row)

    for row in read_table(connection, "runs", "id"):
        legacy_endpoint = row["machine_id"]
        run = Run(
            customer_id=customer_id,
            endpoint_id=None if legacy_endpoint is None else endpoint_ids[legacy_endpoint],
            endpoint_snapshot=row["machine_snapshot"],
            model_id=row["model_id"],
            params=row["params"],
            comment=row["comment"],
            group_names=row["group_names"],
            llm_info=row["llm_info"],
            status=row["status"],
            archived_at=to_utc(row["archived_at"]),
            created_at=to_utc_required(row["created_at"]),
            started_at=to_utc(row["started_at"]),
            finished_at=to_utc(row["finished_at"]),
        )
        session.add(run)
        await session.flush()
        summary.runs += 1

        payload = [
            _result_values(result, case_ids, prompt_for_case, versions, summary)
            for result in results_by_run.get(row["id"], [])
        ]
        await insert_run_results(scope, session, run.id, payload)
        summary.run_results += len(payload)


def _result_values(
    row: sqlite3.Row,
    case_ids: dict[int, int],
    prompt_for_case: dict[int, int],
    versions: dict[int, VersionRef],
    summary: Summary,
) -> dict[str, Any]:
    legacy_case = row["prompt_id"]
    prompt_id = None if legacy_case is None else prompt_for_case.get(legacy_case)
    candidates = [] if prompt_id is None else [versions[prompt_id]]
    version_id = match_version(row["system_prompt_text"], candidates)

    if version_id is not None:
        summary.attributed += 1
    elif row["system_prompt_text"] is None:
        summary.unattributed_no_system_prompt += 1
    else:
        summary.unattributed_text_differs += 1

    return {
        "test_case_id": None if legacy_case is None else case_ids.get(legacy_case),
        "system_prompt_version_id": version_id,
        "task_prompt_version_id": None,
        "sort_order": row["sort_order"],
        "group_name": row["group_name"],
        "test_case_title": row["prompt_title"],
        "test_case_text": row["prompt_text"],
        "expected_output": row["expected_output"],
        "system_prompt_text": row["system_prompt_text"],
        "task_prompt_text": None,
        "tools_snapshot": row["tools_snapshot"],
        "tool_mode": row["tool_mode"],
        "tool_choice": row["tool_choice"],
        "max_turns": row["max_turns"],
        "status": row["status"],
        "response_text": row["response_text"],
        "transcript_json": row["transcript_json"],
        "turns_json": row["turns_json"],
        "turn_count": row["turn_count"],
        "tool_call_count": row["tool_call_count"],
        "stopped_reason": row["stopped_reason"],
        "error": row["error"],
        "duration_ms": row["duration_ms"],
        "ttft_ms": row["ttft_ms"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "tokens_per_sec": row["tokens_per_sec"],
        "tokens_estimated": bool(row["tokens_estimated"]),
        "rating": row["rating"],
        "rating_note": row["rating_note"],
        "started_at": to_utc(row["started_at"]),
        "finished_at": to_utc(row["finished_at"]),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


class _DryRun(Exception):
    """Carries the summary out through the transaction block, which rolls back
    on the way — a rollback by exception rather than by a flag nobody can forget
    to honour.
    """

    def __init__(self, summary: Summary) -> None:
        super().__init__("dry run")
        self.summary = summary


async def run(sqlite_path: Path, customer_name: str, dry_run: bool) -> Summary:
    connection = open_legacy(sqlite_path)
    try:
        async with async_session() as session:
            try:
                async with session.begin():
                    summary = await import_legacy(session, connection, customer_name)
                    if dry_run:
                        raise _DryRun(summary)
            except _DryRun as rolled_back:
                return rolled_back.summary
            return summary
    finally:
        connection.close()
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the retired modelfit SQLite database into a PromptRack workspace."
    )
    parser.add_argument(
        "--sqlite", required=True, type=Path, help="path to the old app's app.db"
    )
    parser.add_argument(
        "--customer",
        default="Webix",
        help="target customer workspace, created if it does not exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="do the whole import, print the counts, then roll back",
    )
    args = parser.parse_args()

    try:
        summary = asyncio.run(run(args.sqlite, args.customer, args.dry_run))
    except LegacyImportError as refusal:
        print(f"Refused: {refusal}", file=sys.stderr)
        return 1

    print(summary.render())
    print()
    print("Rolled back (--dry-run)." if args.dry_run else "Committed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
