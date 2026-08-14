"""`/api/test-cases` — the regression suite: one input, its rubric, and the
tool configuration to run it with.

A test case holds no prompt text of its own. It references up to two prompt
assets by slot — `system_prompt_id` (sent as the system message) and
`task_prompt_id` (sent at the head of the user message) — plus its own
`content`, the data half of the user message. Both slots are checked by
`app.repos.prompts.assert_prompt_slot` from inside the repository functions:
same workspace **and** matching `kind`, in one read.

Test cases hold no credentials, so — like prompts and test groups — mutation
sits at `Writer` and reads at `CurrentUser`. `assert_tool_config`
(`app.services.tool_config`) runs on every create and on any patch that
touches `tool_mode` or `toolset_ids`, re-checked against the *effective*
configuration (existing values merged with the patch) exactly the way the old
MCP `update_prompt` re-checked after a patch — so a test case saved through
this API can never be one run creation (which shares the same
`assert_tool_config`) would later refuse.

The second shared guard works the same way:
`app.services.message_assembly.assert_user_message` refuses a case with
neither a task prompt nor non-blank `content`, because that request has no
user message at all. It lives inside the repository functions too, evaluated
on the *merged* post-patch state, and runs again at run creation.
"""

from collections.abc import Sequence
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.guards import CurrentScope, CurrentUser, DbSession, Writer
from app.models import TestCase, ToolChoice, ToolMode
from app.repos.prompts import PromptSlotError, list_prompts_by_ids
from app.repos.test_cases import (
    create_test_case,
    delete_test_case,
    get_test_case,
    list_test_cases,
    list_toolset_links,
    replace_toolset_links,
    update_test_case,
)
from app.scope import CrossCustomerError, Scope
from app.services.message_assembly import NoUserMessageError
from app.services.tool_config import (
    DEFAULT_MAX_TURNS,
    ToolConfigError,
    assert_tool_config,
    normalize_max_turns,
)

router = APIRouter(prefix="/test-cases", tags=["test-cases"])


# --------------------------------------------------------------------------
# Wire shapes
# --------------------------------------------------------------------------


class TestCaseView(BaseModel):
    id: int
    group_id: int
    title: str
    #: The data half of the user message. Nullable: a task prompt can be the
    #: whole user message on its own ("this prompt takes no input").
    content: str | None
    expected_output: str | None
    system_prompt_id: int | None
    #: The referenced prompt's current name, resolved server-side so the list
    #: never renders a bare id. `None` when the slot is empty, or when that
    #: prompt has since been deleted (`SET NULL`).
    system_prompt_name: str | None
    task_prompt_id: int | None
    #: Same as `system_prompt_name`, for the task slot.
    task_prompt_name: str | None
    tool_mode: ToolMode
    tool_choice: ToolChoice | None
    max_turns: int
    toolset_ids: list[int]
    sort_order: int
    created_at: datetime
    updated_at: datetime


class TestCaseWriteRequest(BaseModel):
    """`content` is optional here, but not free: the server refuses a case
    where **both** `task_prompt_id` and `content` resolve to blank, since that
    request would carry no user message at all. Only the title is required
    unconditionally.
    """

    group_id: int
    title: str = Field(min_length=1)
    content: str | None = None
    expected_output: str | None = None
    system_prompt_id: int | None = None
    task_prompt_id: int | None = None
    tool_mode: ToolMode = "none"
    tool_choice: ToolChoice | None = None
    max_turns: int = DEFAULT_MAX_TURNS
    toolset_ids: list[int] = Field(default_factory=list)
    sort_order: int = 0

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field is required.")
        return cleaned

    @field_validator("content", "expected_output")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("max_turns")
    @classmethod
    def _clamp_max_turns(cls, value: int) -> int:
        return normalize_max_turns(value)

    @field_validator("toolset_ids")
    @classmethod
    def _dedupe_toolset_ids(cls, value: list[int]) -> list[int]:
        deduped: list[int] = []
        for toolset_id in value:
            if toolset_id not in deduped:
                deduped.append(toolset_id)
        return deduped


class TestCasePatchRequest(BaseModel):
    """Partial update — only the fields actually present in the body change,
    matching `PromptPatchRequest`'s convention. `toolset_ids` is the one field
    where "present but empty" (`[]`) is meaningfully different from "absent":
    the former clears every link, the latter leaves them untouched.
    """

    group_id: int | None = None
    title: str | None = None
    content: str | None = None
    expected_output: str | None = None
    system_prompt_id: int | None = None
    task_prompt_id: int | None = None
    tool_mode: ToolMode | None = None
    tool_choice: ToolChoice | None = None
    max_turns: int | None = None
    toolset_ids: list[int] | None = None
    sort_order: int | None = None

    @field_validator("title")
    @classmethod
    def _not_blank_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    #: Blank `content` is legal now — a task prompt may supply the whole user
    #: message — so a present-but-empty value clears the column rather than
    #: being refused. `assert_user_message` is what catches the case where
    #: clearing it leaves nothing to send.
    @field_validator("content", "expected_output")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("max_turns")
    @classmethod
    def _clamp_max_turns_if_present(cls, value: int | None) -> int | None:
        return None if value is None else normalize_max_turns(value)

    @field_validator("toolset_ids")
    @classmethod
    def _dedupe_toolset_ids_if_present(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        deduped: list[int] = []
        for toolset_id in value:
            if toolset_id not in deduped:
                deduped.append(toolset_id)
        return deduped


# --------------------------------------------------------------------------
# View builders / lookups
# --------------------------------------------------------------------------


def _view(
    test_case: TestCase,
    toolset_ids: list[int],
    system_prompt_name: str | None,
    task_prompt_name: str | None,
) -> TestCaseView:
    return TestCaseView(
        id=test_case.id,
        group_id=test_case.group_id,
        title=test_case.title,
        content=test_case.content,
        expected_output=test_case.expected_output,
        system_prompt_id=test_case.system_prompt_id,
        system_prompt_name=system_prompt_name,
        task_prompt_id=test_case.task_prompt_id,
        task_prompt_name=task_prompt_name,
        tool_mode=test_case.tool_mode,
        tool_choice=test_case.tool_choice,
        max_turns=test_case.max_turns,
        toolset_ids=toolset_ids,
        sort_order=test_case.sort_order,
        created_at=test_case.created_at,
        updated_at=test_case.updated_at,
    )


async def _get_or_404(scope: Scope, session: AsyncSession, test_case_id: int) -> TestCase:
    test_case = await get_test_case(scope, session, test_case_id)
    if test_case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such test case.")
    return test_case


async def _toolset_ids_by_case(
    scope: Scope, session: AsyncSession, test_case_ids: list[int]
) -> dict[int, list[int]]:
    links = await list_toolset_links(scope, session, test_case_ids)
    grouped: dict[int, list[int]] = {test_case_id: [] for test_case_id in test_case_ids}
    for link in links:
        grouped.setdefault(link.test_case_id, []).append(link.toolset_id)
    return grouped


async def _prompt_names_by_case(
    scope: Scope, session: AsyncSession, test_cases: Sequence[TestCase]
) -> dict[int, str]:
    """The referenced prompts' current names for a batch of test cases, keyed
    by prompt **id** — both slots collected into one query for a whole list
    rather than one per row and per slot. A slot id absent from this dict
    (including `None` itself) means "no name to show": an empty slot, or a
    prompt deleted since (`SET NULL`).
    """
    prompt_ids = {
        prompt_id
        for tc in test_cases
        for prompt_id in (tc.system_prompt_id, tc.task_prompt_id)
        if prompt_id is not None
    }
    if not prompt_ids:
        return {}
    prompts = await list_prompts_by_ids(scope, session, list(prompt_ids))
    return {prompt.id: prompt.name for prompt in prompts}


async def _view_by_id(scope: Scope, session: AsyncSession, test_case_id: int) -> TestCaseView:
    test_case = await _get_or_404(scope, session, test_case_id)
    toolset_ids = (await _toolset_ids_by_case(scope, session, [test_case_id])).get(
        test_case_id, []
    )
    prompt_names = await _prompt_names_by_case(scope, session, [test_case])
    return _view(
        test_case,
        toolset_ids,
        prompt_names.get(test_case.system_prompt_id),
        prompt_names.get(test_case.task_prompt_id),
    )


# --------------------------------------------------------------------------
# Test case CRUD
# --------------------------------------------------------------------------


@router.get("")
async def list_test_cases_endpoint(
    actor: CurrentUser,
    scope: CurrentScope,
    session: DbSession,
    group_id: int | None = None,
) -> list[TestCaseView]:
    del actor
    test_cases = await list_test_cases(scope, session, group_id=group_id)
    toolset_ids = await _toolset_ids_by_case(scope, session, [tc.id for tc in test_cases])
    prompt_names = await _prompt_names_by_case(scope, session, test_cases)
    return [
        _view(
            tc,
            toolset_ids.get(tc.id, []),
            prompt_names.get(tc.system_prompt_id),
            prompt_names.get(tc.task_prompt_id),
        )
        for tc in test_cases
    ]


@router.get("/{test_case_id}")
async def get_test_case_endpoint(
    test_case_id: int, actor: CurrentUser, scope: CurrentScope, session: DbSession
) -> TestCaseView:
    del actor
    return await _view_by_id(scope, session, test_case_id)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_test_case_endpoint(
    body: TestCaseWriteRequest, actor: Writer, scope: CurrentScope, session: DbSession
) -> TestCaseView:
    del actor
    try:
        await assert_tool_config(
            scope,
            session,
            tool_mode=body.tool_mode,
            toolset_ids=body.toolset_ids,
            subject=f'Test case "{body.title}"',
        )
        test_case = await create_test_case(
            scope,
            session,
            group_id=body.group_id,
            title=body.title,
            content=body.content,
            expected_output=body.expected_output,
            system_prompt_id=body.system_prompt_id,
            task_prompt_id=body.task_prompt_id,
            tool_mode=body.tool_mode,
            tool_choice=body.tool_choice,
            max_turns=body.max_turns,
            sort_order=body.sort_order,
        )
        await replace_toolset_links(scope, session, test_case.id, body.toolset_ids)
    except CrossCustomerError as exc:
        # A prompt (or group, or toolset) that is not in this workspace reads
        # as "no such row" — a 404, not a hint that it exists elsewhere.
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ToolConfigError, PromptSlotError, NoUserMessageError) as exc:
        # A wrong-kind prompt and a case with no user message are both bad
        # requests about rows that do exist here.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await session.commit()
    prompt_names = await _prompt_names_by_case(scope, session, [test_case])
    return _view(
        test_case,
        body.toolset_ids,
        prompt_names.get(test_case.system_prompt_id),
        prompt_names.get(test_case.task_prompt_id),
    )


@router.patch("/{test_case_id}")
async def patch_test_case_endpoint(
    test_case_id: int,
    body: TestCasePatchRequest,
    actor: Writer,
    scope: CurrentScope,
    session: DbSession,
) -> TestCaseView:
    del actor
    existing = await _get_or_404(scope, session, test_case_id)

    values: dict[str, object] = {}
    for field in (
        "group_id",
        "title",
        "content",
        "expected_output",
        "system_prompt_id",
        "task_prompt_id",
        "tool_mode",
        "tool_choice",
        "max_turns",
        "sort_order",
    ):
        if field in body.model_fields_set:
            values[field] = getattr(body, field)

    # Re-checked against the configuration as it will be *after* the patch —
    # switching `tool_mode` without naming `toolset_ids` keeps the existing
    # links and validates against those, not against an empty set.
    effective_tool_mode = values.get("tool_mode", existing.tool_mode)
    if body.toolset_ids is not None:
        effective_toolset_ids = body.toolset_ids
    else:
        existing_links = await _toolset_ids_by_case(scope, session, [test_case_id])
        effective_toolset_ids = existing_links.get(test_case_id, [])

    try:
        await assert_tool_config(
            scope,
            session,
            tool_mode=effective_tool_mode,
            toolset_ids=effective_toolset_ids,
            subject=f'Test case "{values.get("title", existing.title)}"',
        )
        if values:
            await update_test_case(scope, session, test_case_id, values)
        if body.toolset_ids is not None:
            await replace_toolset_links(scope, session, test_case_id, body.toolset_ids)
    except CrossCustomerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ToolConfigError, PromptSlotError, NoUserMessageError) as exc:
        # `update_test_case` evaluates the user-message guard on the *merged*
        # post-patch row, so a patch that only clears `content` on a case with
        # no task prompt is refused here rather than surviving as a case run
        # creation would later reject.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    await session.commit()
    return await _view_by_id(scope, session, test_case_id)


@router.delete("/{test_case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_test_case_endpoint(
    test_case_id: int, actor: Writer, scope: CurrentScope, session: DbSession
) -> None:
    """Deletes the case only. Past runs are unaffected — they carry their own
    frozen snapshot, not a live reference to this row.
    """
    del actor
    await _get_or_404(scope, session, test_case_id)
    await delete_test_case(scope, session, test_case_id)
    await session.commit()
