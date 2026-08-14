"""The scope primitive, database-free.

Everything here is pure: predicates are inspected as compiled SQL text rather
than executed, so this suite stays in the fast `pytest` run and never needs
Postgres. The wired-up half — that a scoped query really cannot see another
workspace's rows — is the integration suite's job.
"""

from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import select

from app.models import Endpoint, Prompt, Run, Toolset

# Aliased away from its own name: pytest tries to *collect* any module-level
# name starting with `Test`, and warns that the ORM class has an `__init__`.
from app.models import TestGroup as GroupModel
from app.repos.scoped import apply_where, scope_through_parent
from app.scope import (
    CustomerOption,
    Scope,
    ScopeError,
    combine,
    require_customer_id,
    resolve_active_customer_id,
    scope_for_customer,
    scope_from_row,
    scope_values,
    scope_where,
    system_scope,
    visible_where,
    where_scoped,
    where_visible,
)


def sql(element: object) -> str:
    return str(element)


A = Run.id == 1
B = Run.status == "completed"


class TestConstruction:
    def test_a_scope_cannot_be_built_directly(self) -> None:
        # The whole guarantee rests on this: if `Scope(...)` worked, "a query
        # without a scope" would be writable again.
        with pytest.raises(ScopeError, match="cannot be constructed directly"):
            Scope(1, "session")

    def test_records_where_a_scope_came_from(self) -> None:
        assert scope_for_customer(1).origin == "session"
        assert scope_from_row(1).origin == "row"
        assert system_scope("backfill").origin == "system"

    def test_names_one_workspace_except_the_system_scope(self) -> None:
        assert scope_for_customer(7).customer_id == 7
        assert scope_from_row(7).customer_id == 7
        assert system_scope("backfill").customer_id is None

    def test_is_frozen(self) -> None:
        scope = scope_for_customer(1)
        with pytest.raises(FrozenInstanceError):
            scope.customer_id = 2  # type: ignore[misc]


class TestCombine:
    def test_collapses_an_empty_list_to_none(self) -> None:
        assert combine([]) is None

    def test_collapses_a_list_of_only_none_to_none(self) -> None:
        assert combine([None, None]) is None

    def test_returns_a_single_condition_unwrapped(self) -> None:
        assert combine([A]) is A
        assert combine([None, A, None]) is A

    def test_ands_several_conditions_together(self) -> None:
        both = combine([A, B])
        assert both is not None
        assert both is not A
        assert " AND " in sql(both)


class TestWhereScoped:
    def test_restricts_a_root_table_to_the_scope_customer(self) -> None:
        where = where_scoped(scope_from_row(7), Run)
        # The predicate is built from the table's own `customer_id` column,
        # which is what makes it impossible to scope a query against the wrong
        # table.
        assert sql(where) == "runs.customer_id = :customer_id_1"

    def test_uses_the_column_of_the_table_it_is_given(self) -> None:
        assert sql(scope_where(scope_from_row(7), Toolset)).startswith("toolsets.customer_id")

    def test_ands_the_caller_conditions_onto_the_scope_predicate(self) -> None:
        where = where_scoped(scope_from_row(7), Run, A)
        assert where is not None
        assert where is not A
        assert "runs.customer_id" in sql(where)
        assert " AND " in sql(where)

    def test_is_a_no_op_under_the_system_scope(self) -> None:
        # A system scope read spans every workspace, which is what it is for.
        assert where_scoped(system_scope("admin"), Run) is None
        assert where_scoped(system_scope("admin"), Run, A) is A


class TestVisibleWhere:
    """The read-side seam: ownership, OR the rows Base shares.

    Only endpoints and toolsets are shareable, and the tests below pin *both*
    halves of that — because the value of the design is entirely in the second
    half: if `visible_where` ever widened to a table that is not shareable, one
    customer's suite would start showing up inside another's.
    """

    def test_widens_the_two_shareable_tables_to_include_globals(self) -> None:
        for model, table in ((Endpoint, "endpoints"), (Toolset, "toolsets")):
            where = sql(visible_where(scope_from_row(7), model))
            assert f"{table}.customer_id = :customer_id_1" in where
            assert f"{table}.is_global IS true" in where
            assert " OR " in where

    def test_is_exactly_scope_where_for_every_other_table(self) -> None:
        # Prompts, test groups and runs are the engagement's own material. A
        # global prompt is not a feature that was left out — it is the thing a
        # workspace exists to prevent.
        for model in (Prompt, GroupModel, Run):
            scope = scope_from_row(7)
            assert sql(visible_where(scope, model)) == sql(scope_where(scope, model))
            assert " OR " not in sql(visible_where(scope, model))

    def test_leaves_scope_where_itself_untouched(self) -> None:
        # The whole mechanism rests on the ownership predicate staying exact:
        # it is what UPDATE, DELETE and `scope_values` keep using, and so what
        # makes "read-only outside Base" cost no new guard.
        assert sql(scope_where(scope_from_row(7), Endpoint)) == (
            "endpoints.customer_id = :customer_id_1"
        )
        assert "is_global" not in sql(scope_where(scope_from_row(7), Toolset))

    def test_is_a_no_op_under_the_system_scope(self) -> None:
        # "Every workspace" already contains the global rows; narrowing here
        # would make the escape hatch see less than an ordinary scope.
        assert visible_where(system_scope("admin"), Endpoint) is None
        assert where_visible(system_scope("admin"), Endpoint) is None
        assert where_visible(system_scope("admin"), Endpoint, A) is A

    def test_ands_the_caller_conditions_onto_the_visibility_predicate(self) -> None:
        where = where_visible(scope_from_row(7), Endpoint, Endpoint.id == 3)
        text = sql(where)
        assert "endpoints.is_global IS true" in text
        assert "endpoints.id = " in text
        assert " AND " in text


class TestScopeValues:
    def test_contributes_the_customer_column_to_an_insert(self) -> None:
        assert scope_values(scope_from_row(3)) == {"customer_id": 3}

    def test_refuses_to_insert_under_the_system_scope(self) -> None:
        # A read may deliberately span workspaces; an insert has no defensible
        # workspace to land in.
        with pytest.raises(ScopeError, match="system scope"):
            scope_values(system_scope("backfill"))
        with pytest.raises(ScopeError, match="system scope"):
            require_customer_id(system_scope("backfill"))

    def test_returns_the_workspace_of_a_normal_scope(self) -> None:
        assert require_customer_id(scope_for_customer(4)) == 4


class TestApplyWhere:
    def test_adds_a_predicate(self) -> None:
        statement = apply_where(select(Run.id), where_scoped(scope_from_row(2), Run))
        assert "WHERE runs.customer_id" in sql(statement)

    def test_leaves_the_statement_alone_when_there_is_no_predicate(self) -> None:
        statement = select(Run.id)
        assert apply_where(statement, None) is statement


class TestScopeThroughParent:
    def test_restricts_a_child_to_parents_in_scope(self) -> None:
        # Child tables carry no `customer_id`; an UPDATE or DELETE cannot join,
        # so the inheritance is expressed as this subquery.
        where = scope_through_parent(scope_from_row(5), Run.id, Run, Run.id)
        text = sql(where)
        assert "IN (SELECT runs.id" in text
        assert "runs.customer_id = :customer_id_1" in text

    def test_is_a_no_op_under_the_system_scope(self) -> None:
        assert scope_through_parent(system_scope("admin"), Run.id, Run, Run.id) is None

    def test_inherits_visibility_only_when_asked(self) -> None:
        # The opt-in, on the seam a child read uses: without it a global
        # endpoint's `endpoint_models` and a global toolset's `tools` are
        # invisible — a missing feature, never a leak, which is why this
        # direction is the default.
        strict = sql(scope_through_parent(scope_from_row(5), Toolset.id, Toolset, Toolset.id))
        assert "is_global" not in strict

        shared = sql(
            scope_through_parent(
                scope_from_row(5), Toolset.id, Toolset, Toolset.id, visible=True
            )
        )
        assert "toolsets.is_global IS true" in shared
        assert "toolsets.customer_id = :customer_id_1" in shared


class TestResolveActiveCustomerId:
    @staticmethod
    def options(*entries: tuple[int, bool]) -> list[CustomerOption]:
        return [
            CustomerOption(id=id_, name=f"w{id_}", archived=archived)
            for id_, archived in entries
        ]

    def test_keeps_a_preferred_workspace_that_is_live(self) -> None:
        assert resolve_active_customer_id(2, self.options((1, False), (2, False))) == 2

    def test_falls_back_when_the_preferred_one_is_archived(self) -> None:
        assert resolve_active_customer_id(2, self.options((1, False), (2, True))) == 1

    def test_falls_back_when_the_preferred_workspace_is_gone(self) -> None:
        assert resolve_active_customer_id(99, self.options((1, False), (2, False))) == 1

    def test_falls_back_when_nothing_is_preferred(self) -> None:
        assert resolve_active_customer_id(None, self.options((3, False), (4, False))) == 3

    def test_uses_an_archived_workspace_rather_than_leaving_the_app_unusable(self) -> None:
        assert resolve_active_customer_id(None, self.options((1, True))) == 1
        assert resolve_active_customer_id(1, self.options((1, True))) == 1

    def test_has_nothing_to_resolve_to_when_no_workspace_exists(self) -> None:
        assert resolve_active_customer_id(None, []) is None
        assert resolve_active_customer_id(5, []) is None
