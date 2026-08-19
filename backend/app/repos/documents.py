"""The markdown corpus behind a `documents` toolset — every scoped query.

`documents` is a second child table of `toolsets`, sitting beside `tools`, and it
carries no `customer_id`: scope is inherited through `toolset_id`, exactly the
way this module's neighbours in `app.repos.toolsets` inherit it for `tools`.
Which predicate a query asks for is the whole of "a borrowed corpus is
read-only":

* **Reads** ask :func:`~app.scope.where_visible` (or `scope_through_parent(...,
  visible=True)` where only an id is known), so a global toolset the Base
  workspace shares brings its documents along into every engagement that
  borrows it — including at execution time, where the run's scope comes from the
  run row and the toolset may well not be its own.
* **Writes** keep asking :func:`~app.scope.where_scoped`. A borrowed corpus is
  therefore not writable at all, with no permission layer involved: the strict
  predicate simply matches nothing. `app.api.toolsets._refuse_if_borrowed` sits
  on top only so the refusal reads as a 403 rather than a silent no-op.

Search is the one thing here that is real SQL rather than a filter, and it is
deliberately all of it: `websearch_to_tsquery` for the query, `ts_rank` for the
order, `ts_headline` for the fragment. Every one of them names
:data:`~app.models.toolsets.DOCUMENT_SEARCH_CONFIG` — the same configuration the
stored `content_tsv` was generated with, because a query parsed under a
different configuration than the vector silently stops matching, and `simple` is
the deliberate choice for a mixed German/English corpus (see
:class:`app.models.toolsets.Document`). Turning a match into a readable snippet
and finding the heading above it is `app.services.documents`' job: the SQL stays
here, the text munging stays pure and testable there.

Nothing in the corpus is ever resolved against a filesystem. `path` is an
identifier, the lookup is `toolset_id` plus this scope's predicate, and a
`path` the model invents therefore selects nothing — a `WHERE` clause rather
than a sanitizer, and no traversal surface to defend.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, Toolset
from app.models.toolsets import DOCUMENT_SEARCH_CONFIG
from app.repos.customers import assert_same_customer
from app.repos.scoped import apply_where, scope_through_parent
from app.scope import Scope, combine, where_visible
from app.services.documents import (
    HEADLINE_OPTIONS,
    DocumentMatch,
    DocumentSummary,
    heading_for_snippet,
    normalize_search_limit,
    shape_snippet,
)


class DocumentPathConflictError(Exception):
    """Two documents in one corpus claiming the same `path`.

    `UNIQUE(toolset_id, path)` is the constraint, but the refusal is raised from
    a pre-check rather than from catching the `IntegrityError` — deliberately the
    opposite of `create_tool`, which lets its unique violation escape for the
    caller to translate. The reason is the upload route: it writes several
    documents in one transaction, and a violation there would poison the whole
    unit of work, losing the files that were fine. The constraint stays the
    backstop for a genuine race.
    """


@dataclass(frozen=True)
class DocumentMeta:
    """One document without its text.

    Everything a list needs — the corpus browser, the `list_documents` tool, the
    "which paths exist" half of a bad-path message — and none of the megabyte
    that would come with `content`. `chars` is measured in the database
    (`char_length`) for the same reason: the length is cheap there and the text
    is not.
    """

    id: int
    toolset_id: int
    title: str
    path: str
    chars: int
    created_at: datetime
    updated_at: datetime

    def summary(self) -> DocumentSummary:
        """The pure shape `app.services.documents` builds its payloads from."""
        return DocumentSummary(path=self.path, title=self.title, chars=self.chars)


@dataclass(frozen=True)
class CorpusStats:
    """How much corpus a toolset holds, and when it last changed.

    Both numbers are written into every `SnapshotTool` a documents toolset
    contributes (`document_count`, `corpus_updated_at`). There is no corpus
    freezing in this version and these do not create one — they are two keys in
    a dict that was being serialized anyway, so that a later version can tell
    "the corpus changed after this run" without needing a migration to find out.
    """

    toolset_id: int
    document_count: int
    updated_at: datetime | None


def document_summary(document: Document) -> DocumentSummary:
    """A full row projected into the pure summary shape.

    `read_document` has the whole row in hand already, so measuring `content`
    here costs nothing — unlike :meth:`DocumentMeta.summary`, which exists
    precisely to avoid loading it.
    """
    return DocumentSummary(
        path=document.path, title=document.title, chars=len(document.content)
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def list_documents(
    scope: Scope, session: AsyncSession, *, toolset_ids: Sequence[int] | None = None
) -> list[DocumentMeta]:
    """Every visible document, ordered by path — mirrors `list_tools`.

    An explicitly empty `toolset_ids` means "no corpora", and answering it
    without a round trip is what keeps a caller from having to special-case a
    test case that selects nothing.
    """
    if toolset_ids is not None and not toolset_ids:
        return []
    statement = apply_where(
        select(
            Document.id,
            Document.toolset_id,
            Document.title,
            Document.path,
            func.char_length(Document.content),
            Document.created_at,
            Document.updated_at,
        ).join(Toolset, Document.toolset_id == Toolset.id),
        where_visible(
            scope,
            Toolset,
            None if toolset_ids is None else Document.toolset_id.in_(list(toolset_ids)),
        ),
    ).order_by(Document.toolset_id.asc(), Document.path.asc())
    rows = await session.execute(statement)
    return [
        DocumentMeta(
            id=row[0],
            toolset_id=row[1],
            title=row[2],
            path=row[3],
            chars=row[4],
            created_at=row[5],
            updated_at=row[6],
        )
        for row in rows.all()
    ]


async def get_document(
    scope: Scope, session: AsyncSession, document_id: int
) -> Document | None:
    """One document with its text, by id."""
    statement = apply_where(
        select(Document).join(Toolset, Document.toolset_id == Toolset.id),
        where_visible(scope, Toolset, Document.id == document_id),
    )
    return (await session.scalars(statement)).first()


async def get_document_by_path(
    scope: Scope, session: AsyncSession, toolset_id: int, path: str
) -> Document | None:
    """One document with its text, by the key `read_document` uses.

    A visible read, not an owned one, because this is the execution path: a run
    against a borrowed global corpus derives its scope from the run row and must
    still be able to read the documents. The match is exact — no normalisation,
    no case folding, no prefix — so that the path the model quotes back from
    `list_documents` is the path it gets, and anything else is an honest miss the
    model is told about.
    """
    statement = apply_where(
        select(Document).join(Toolset, Document.toolset_id == Toolset.id),
        where_visible(scope, Toolset, Document.toolset_id == toolset_id, Document.path == path),
    )
    return (await session.scalars(statement)).first()


async def list_corpus_stats(
    scope: Scope, session: AsyncSession, toolset_ids: Sequence[int]
) -> dict[int, CorpusStats]:
    """Document count and newest `updated_at` per toolset.

    Every requested id gets an entry, zero-filled when the corpus is empty or
    invisible to this scope, so a caller freezing a snapshot never has to decide
    what a missing key means.
    """
    stats = {
        toolset_id: CorpusStats(toolset_id=toolset_id, document_count=0, updated_at=None)
        for toolset_id in toolset_ids
    }
    if not stats:
        return stats

    statement = apply_where(
        select(
            Document.toolset_id,
            func.count(Document.id),
            func.max(Document.updated_at),
        ).join(Toolset, Document.toolset_id == Toolset.id),
        where_visible(scope, Toolset, Document.toolset_id.in_(list(stats))),
    ).group_by(Document.toolset_id)
    for toolset_id, count, updated_at in (await session.execute(statement)).all():
        stats[toolset_id] = CorpusStats(
            toolset_id=toolset_id, document_count=count, updated_at=updated_at
        )
    return stats


async def search_documents(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    *,
    query: str,
    limit: int | None = None,
) -> list[DocumentMatch]:
    """Full-text search across one corpus, best passage per document.

    The configuration is named three times over — in the query, in the ranking
    and in the fragment — and all three have to be
    :data:`~app.models.toolsets.DOCUMENT_SEARCH_CONFIG`, which is why it is a
    constant rather than a string typed out per call site. Each is cast to
    `regconfig` explicitly: a bound parameter arriving as `text` leaves Postgres
    with no matching two-argument overload at all.

    `content` comes back with each hit because the heading a snippet sits under
    is resolved in Python from the document's own text
    (`app.services.documents.heading_for_snippet`) — a bounded cost, since
    `limit` is clamped well below any corpus size.

    A blank query answers nothing without a round trip. `websearch_to_tsquery`
    would happily parse it into an empty tsquery that matches nothing, but the
    model is better served by the empty-result note than by an empty search
    looking like a broken corpus.
    """
    text = query.strip()
    if not text:
        return []

    config = cast(DOCUMENT_SEARCH_CONFIG, REGCONFIG)
    tsquery = func.websearch_to_tsquery(config, text)
    rank = func.ts_rank(Document.content_tsv, tsquery)
    headline = func.ts_headline(config, Document.content, tsquery, HEADLINE_OPTIONS)

    statement = (
        apply_where(
            select(Document.id, Document.path, Document.title, Document.content, rank, headline)
            .join(Toolset, Document.toolset_id == Toolset.id),
            where_visible(
                scope,
                Toolset,
                Document.toolset_id == toolset_id,
                Document.content_tsv.bool_op("@@")(tsquery),
            ),
        )
        # `path` breaks ties so two equally-ranked documents come back in a
        # stable order: a retrieval measurement that reshuffles between runs is
        # unreadable in `/results`.
        .order_by(rank.desc(), Document.path.asc())
        .limit(normalize_search_limit(limit))
    )
    rows = await session.execute(statement)
    return [
        DocumentMatch(
            document_id=row[0],
            path=row[1],
            title=row[2],
            heading=heading_for_snippet(row[3], row[5]),
            snippet=shape_snippet(row[5]),
            rank=float(row[4]),
        )
        for row in rows.all()
    ]


# ---------------------------------------------------------------------------
# Writes — ownership, never visibility
# ---------------------------------------------------------------------------


def _document_where(scope: Scope, document_id: int):
    """`documents` is a child of `toolsets`, so a write that only knows a
    document id inherits its scope through the toolset it belongs to — the same
    subquery `app.repos.toolsets._tool_where` uses, and deliberately without
    `visible=True`: a borrowed corpus is read-only.
    """
    return combine(
        [
            Document.id == document_id,
            scope_through_parent(scope, Document.toolset_id, Toolset, Toolset.id),
        ]
    )


async def _assert_path_free(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    path: str,
    *,
    exclude_id: int | None = None,
) -> None:
    statement = apply_where(
        select(Document.id),
        combine(
            [
                Document.toolset_id == toolset_id,
                Document.path == path,
                None if exclude_id is None else Document.id != exclude_id,
                scope_through_parent(scope, Document.toolset_id, Toolset, Toolset.id),
            ]
        ),
    )
    if (await session.scalars(statement)).first() is not None:
        raise DocumentPathConflictError(f'This corpus already has a document at "{path}".')


async def create_document(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    *,
    title: str,
    path: str,
    content: str,
) -> Document:
    """Adds one document to a corpus.

    Ownership-checked and without `allow_global`, exactly like `create_tool`:
    documents are authored where their toolset lives, which for a shared corpus
    means in Base and nowhere else.

    `content_tsv` is never named — it is a STORED generated column, so Postgres
    computes it from the values below and any attempt to write it is an error.
    """
    await assert_same_customer(session, scope, Toolset, toolset_id)
    await _assert_path_free(scope, session, toolset_id, path)
    document = Document(toolset_id=toolset_id, title=title, path=path, content=content)
    session.add(document)
    await session.flush()
    return document


async def update_document(
    scope: Scope, session: AsyncSession, document_id: int, values: Mapping[str, Any]
) -> None:
    """Patches a document this workspace owns.

    A patch that moves the document to a `path` its corpus already uses is
    refused before the `UPDATE`, so a refused edit writes nothing. `updated_at`
    is the column's own `onupdate`, and it is what
    :class:`CorpusStats`' `updated_at` reads.
    """
    if not values:
        return
    if "path" in values:
        existing = (
            await session.scalars(
                apply_where(select(Document), _document_where(scope, document_id))
            )
        ).first()
        if existing is None:
            return
        await _assert_path_free(
            scope,
            session,
            existing.toolset_id,
            str(values["path"]),
            exclude_id=document_id,
        )
    statement = apply_where(update(Document), _document_where(scope, document_id))
    await session.execute(statement.values(**values))


async def delete_document(scope: Scope, session: AsyncSession, document_id: int) -> None:
    await session.execute(apply_where(delete(Document), _document_where(scope, document_id)))


@dataclass(frozen=True)
class DocumentWrite:
    """What :func:`upsert_document` did, for a caller reporting an upload."""

    document: Document
    created: bool


async def upsert_document(
    scope: Scope,
    session: AsyncSession,
    toolset_id: int,
    *,
    title: str,
    path: str,
    content: str,
) -> DocumentWrite:
    """Writes a document, replacing whatever was at that `path`.

    Path-idempotent on purpose — the same shape `create_test_group` has over
    MCP. Uploading a folder twice, or re-uploading one corrected file, is the
    ordinary way a corpus is maintained, and a conflict on the second attempt
    would make the obvious action the wrong one. Editing a document by id
    (:func:`update_document`) is where a path collision is still a genuine
    mistake and stays refused.
    """
    await assert_same_customer(session, scope, Toolset, toolset_id)
    existing = (
        await session.scalars(
            apply_where(
                select(Document),
                combine(
                    [
                        Document.toolset_id == toolset_id,
                        Document.path == path,
                        scope_through_parent(scope, Document.toolset_id, Toolset, Toolset.id),
                    ]
                ),
            )
        )
    ).first()
    if existing is None:
        document = Document(toolset_id=toolset_id, title=title, path=path, content=content)
        session.add(document)
        await session.flush()
        return DocumentWrite(document=document, created=True)

    existing.title = title
    existing.content = content
    await session.flush()
    return DocumentWrite(document=existing, created=False)
