"""`/api/toolsets/{id}/documents` end to end: real app, real Postgres, role gating.

The corpus is the one part of a toolset that is **content**, and almost
everything worth asserting here is a consequence of that: a `Writer` fills it
while only an `Admin` may create the container it hangs in, a borrowed global
corpus reads from every workspace and writes from none, and the three retrieval
tools are rows that arrive with the toolset rather than being authored.

What only the wired-up route can show, and therefore what this file is for:
the path normalisation and title derivation really land in the stored columns
(not merely in a response), a per-file upload rejection costs that file and
nothing else, and the two refusals that exist purely so a caller is not told a
no-op succeeded (`_refuse_if_borrowed`) or that a corpus no model can reach was
accepted (`_require_documents_kind`).

The multipart route is exercised with real bytes — a wrong extension, a latin-1
byte sequence and a file over the per-document cap — because "which of these
thirty files did not make it, and why" is the whole point of that response
shape and nothing below the HTTP layer can produce it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import users as user_store
from app.auth.passwords import hash_password
from app.auth.policy import Role
from app.db import async_session
from app.main import app
from app.models import Customer, Document
from app.repos.documents import create_document
from app.repos.toolsets import create_toolset
from app.scope import Scope
from app.services.documents import DOCUMENT_TOOL_NAMES

CreateWorkspace = Callable[[str], Awaitable[tuple[int, Scope]]]

PASSWORD = "correct horse battery staple"

REFUNDS = """# Rückgaberichtlinie

Kunden können innerhalb von 30 Tagen zurückgeben.

## Refunds after 30 days

A refund past thirty days needs warehouse approval.
"""


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def make_user(
    session: AsyncSession,
    email: str,
    role: Role,
    active_customer_id: int | None = None,
    password: str = PASSWORD,
) -> int:
    user = await user_store.create_user(
        session, email=email, name=email, password_hash=hash_password(password), role=role
    )
    if active_customer_id is not None:
        await user_store.set_active_customer_id(session, user.id, active_customer_id)
    await session.commit()
    return user.id


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> None:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


async def _make_base(
    session: AsyncSession, create_workspace: CreateWorkspace, name: str = "Base"
) -> tuple[int, Scope]:
    """A Base workspace, flagged the way the migration flags it — see
    `tests/integration/test_workspaces.py`, which explains why this is a direct
    UPDATE rather than a repository call.
    """
    customer_id, scope = await create_workspace(name)
    await session.execute(
        update(Customer).where(Customer.id == customer_id).values(is_base=True)
    )
    return customer_id, scope


async def _signed_in_corpus(
    client: AsyncClient,
    session: AsyncSession,
    create_workspace: CreateWorkspace,
    *,
    role: Role = "member",
) -> int:
    """A documents toolset in a fresh workspace, with `role` signed in.

    Created through the repository rather than through `POST /api/toolsets`,
    because the container is `Admin` and almost every test below is about a
    `Writer` filling it — which is exactly the split this feature relies on.
    """
    customer_id, scope = await create_workspace("Acme")
    toolset = await create_toolset(scope, session, name="Handbook", kind="documents")
    toolset_id = toolset.id
    await session.commit()
    await make_user(session, f"{role}@example.com", role, customer_id)
    await login(client, f"{role}@example.com")
    return toolset_id


def _upload(name: str, body: bytes) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (name, body, "text/markdown"))


class TestDocumentCrud:
    async def test_a_writer_creates_lists_and_reads_a_document(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)

        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        # The title came from the markdown's own first heading, not from the path.
        assert body["title"] == "Rückgaberichtlinie"
        assert body["path"] == "guides/refunds.md"
        assert body["chars"] == len(REFUNDS)
        assert body["content"] == REFUNDS
        assert body["toolset_id"] == toolset_id

        listed = await client.get(f"/api/toolsets/{toolset_id}/documents")
        assert listed.status_code == 200
        rows = listed.json()
        assert [row["path"] for row in rows] == ["guides/refunds.md"]
        # The list is metadata only — a corpus is megabytes.
        assert "content" not in rows[0]

        got = await client.get(f"/api/toolsets/{toolset_id}/documents/{body['id']}")
        assert got.status_code == 200
        assert got.json()["content"] == REFUNDS

    async def test_the_path_is_normalised_and_line_endings_are_lf(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Asserted against the **stored column**, not the response.

        `read_document` windows by characters and reports those offsets back to
        the model, so a corpus that kept whichever line endings the last editor
        wrote would hand out windows of a length nobody can predict. And a path
        stored as both `guides/x.md` and `/guides/x.md` would make the key the
        model quotes back from a listing a coin flip.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)

        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": ".\\guides\\.\\refunds.MD", "content": "# Title\r\nBody\r\n"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["path"] == "guides/refunds.MD"

        async with async_session() as fresh:
            stored = await fresh.get(Document, created.json()["id"])
            assert stored is not None
            assert stored.path == "guides/refunds.MD"
            assert stored.content == "# Title\nBody\n"
            assert stored.title == "Title"

    async def test_a_duplicate_path_is_refused(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        first = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        assert first.status_code == 201, first.text

        again = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": "# Something else"},
        )
        assert again.status_code == 409
        assert 'already has a document at "guides/refunds.md"' in again.json()["message"]

        # And the first document is untouched: the refusal is a pre-check, so
        # nothing was written.
        detail = await client.get(f"/api/toolsets/{toolset_id}/documents/{first.json()['id']}")
        assert detail.json()["content"] == REFUNDS

    async def test_a_blank_path_or_blank_markdown_is_a_422(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)

        for path in ("   ", "..", "guides/../refunds.md", "./"):
            refused = await client.post(
                f"/api/toolsets/{toolset_id}/documents",
                json={"path": path, "content": "# ok"},
            )
            assert refused.status_code == 422, f"{path!r} -> {refused.status_code}"

        blank = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "empty.md", "content": "   \n\n "},
        )
        assert blank.status_code == 422

    async def test_updating_a_document_replaces_all_three_fields(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "refunds.md", "content": REFUNDS},
        )
        document_id = created.json()["id"]

        updated = await client.put(
            f"/api/toolsets/{toolset_id}/documents/{document_id}",
            json={
                "path": "guides/refunds.md",
                "title": "Refund policy",
                "content": "# Refund policy\n\nThirty days.\n",
            },
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert (body["path"], body["title"]) == ("guides/refunds.md", "Refund policy")
        assert body["content"] == "# Refund policy\n\nThirty days.\n"
        assert body["chars"] == len("# Refund policy\n\nThirty days.\n")

        async with async_session() as fresh:
            stored = await fresh.get(Document, document_id)
            assert stored is not None
            assert stored.path == "guides/refunds.md"
            assert stored.content == "# Refund policy\n\nThirty days.\n"

    async def test_moving_a_document_onto_a_taken_path_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Unlike the upload route, where replacing by path is the point."""
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        second = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/shipping.md", "content": "# Versand\n\nDHL.\n"},
        )
        second_id = second.json()["id"]

        refused = await client.put(
            f"/api/toolsets/{toolset_id}/documents/{second_id}",
            json={"path": "guides/refunds.md", "content": "# Versand\n\nDHL.\n"},
        )
        assert refused.status_code == 409
        assert 'already has a document at "guides/refunds.md"' in refused.json()["message"]

        async with async_session() as fresh:
            stored = await fresh.get(Document, second_id)
            assert stored is not None
            assert stored.path == "guides/shipping.md"

    async def test_deleting_a_document_leaves_the_three_tools_alone(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """An emptied corpus still answers all three tools truthfully — the
        alternative is a tool vanishing mid-suite, which reads as a different
        failure entirely.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        document_id = created.json()["id"]

        deleted = await client.delete(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        assert deleted.status_code == 204
        assert (
            await client.get(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        ).status_code == 404

        detail = (await client.get(f"/api/toolsets/{toolset_id}")).json()
        assert detail["documents"] == []
        assert detail["document_count"] == 0
        assert sorted(tool["name"] for tool in detail["tools"]) == sorted(DOCUMENT_TOOL_NAMES)

    async def test_a_document_from_another_toolset_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Addressed through its toolset, like a tool: a document id the caller
        did not name the container of must not resolve here.
        """
        customer_id, scope = await create_workspace("Acme")
        one = await create_toolset(scope, session, name="One", kind="documents")
        two = await create_toolset(scope, session, name="Two", kind="documents")
        one_id, two_id = one.id, two.id
        document = await create_document(
            scope, session, one_id, title="Refunds", path="guides/refunds.md", content=REFUNDS
        )
        document_id = document.id
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        assert (
            await client.get(f"/api/toolsets/{two_id}/documents/{document_id}")
        ).status_code == 404
        assert (
            await client.delete(f"/api/toolsets/{two_id}/documents/{document_id}")
        ).status_code == 404
        assert (
            await client.get(f"/api/toolsets/{one_id}/documents/{document_id}")
        ).status_code == 200

    async def test_a_toolset_in_another_workspace_is_a_404(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        _, scope_a = await create_workspace("A")
        toolset_a = await create_toolset(scope_a, session, name="A docs", kind="documents")
        toolset_a_id = toolset_a.id
        customer_b, _ = await create_workspace("B")
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_b)
        await login(client, "member@example.com")

        assert (await client.get(f"/api/toolsets/{toolset_a_id}/documents")).status_code == 404
        assert (
            await client.post(
                f"/api/toolsets/{toolset_a_id}/documents",
                json={"path": "x.md", "content": "# x"},
            )
        ).status_code == 404


class TestDocumentRoles:
    async def test_a_member_fills_a_corpus_they_cannot_create(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The whole role split in one test: the container may hold credentials
        and is `Admin`; the markdown inside it never does and is `Writer`.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)

        refused = await client.post("/api/toolsets", json={"name": "mine", "kind": "documents"})
        assert refused.status_code == 403

        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        assert created.status_code == 201, created.text
        assert (await client.post(f"/api/toolsets/{toolset_id}/documents/sync")).status_code == 200

    async def test_a_viewer_reads_the_corpus_and_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Handbook", kind="documents")
        toolset_id = toolset.id
        document = await create_document(
            scope, session, toolset_id, title="Refunds", path="guides/refunds.md", content=REFUNDS
        )
        document_id = document.id
        await session.commit()
        await make_user(session, "viewer@example.com", "viewer", customer_id)
        await login(client, "viewer@example.com")

        assert (await client.get(f"/api/toolsets/{toolset_id}/documents")).status_code == 200
        assert (
            await client.get(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        ).status_code == 200

        assert (
            await client.post(
                f"/api/toolsets/{toolset_id}/documents",
                json={"path": "x.md", "content": "# x"},
            )
        ).status_code == 403
        assert (
            await client.put(
                f"/api/toolsets/{toolset_id}/documents/{document_id}",
                json={"path": "guides/refunds.md", "content": "# hijacked"},
            )
        ).status_code == 403
        assert (
            await client.delete(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        ).status_code == 403
        assert (
            await client.post(f"/api/toolsets/{toolset_id}/documents/sync")
        ).status_code == 403
        assert (
            await client.post(
                f"/api/toolsets/{toolset_id}/documents/upload",
                files=[_upload("x.md", b"# x")],
            )
        ).status_code == 403


class TestDocumentsKindGate:
    async def test_filling_a_corpus_on_a_manual_toolset_is_refused_but_reading_is_not(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Only the three routes that *fill* a corpus ask the kind.

        A manual toolset with documents in it would be a corpus no model can
        reach, so saying so beats accepting the upload. The reads answer `[]`
        rather than 400 for the opposite reason: a toolset switched away from
        `documents` keeps its corpus, and a `Writer` who can see a document has
        to be able to correct or delete it.
        """
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Desk", kind="manual")
        toolset_id = toolset.id
        await session.commit()
        await make_user(session, "member@example.com", "member", customer_id)
        await login(client, "member@example.com")

        created = await client.post(
            f"/api/toolsets/{toolset_id}/documents",
            json={"path": "guides/refunds.md", "content": REFUNDS},
        )
        assert created.status_code == 400
        assert "not a documents toolset" in created.json()["message"]

        upload = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload", files=[_upload("x.md", b"# x")]
        )
        assert upload.status_code == 400
        sync = await client.post(f"/api/toolsets/{toolset_id}/documents/sync")
        assert sync.status_code == 400

        listed = await client.get(f"/api/toolsets/{toolset_id}/documents")
        assert listed.status_code == 200
        assert listed.json() == []
        detail = (await client.get(f"/api/toolsets/{toolset_id}")).json()
        assert detail["documents"] == []
        assert detail["document_count"] == 0

    async def test_a_corpus_survives_its_toolset_being_switched_away(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The reason `PUT`/`DELETE` are deliberately not gated on the kind."""
        customer_id, scope = await create_workspace("Acme")
        toolset = await create_toolset(scope, session, name="Handbook", kind="documents")
        toolset_id = toolset.id
        document = await create_document(
            scope, session, toolset_id, title="Refunds", path="guides/refunds.md", content=REFUNDS
        )
        document_id = document.id
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        converted = await client.put(
            f"/api/toolsets/{toolset_id}", json={"name": "Handbook", "kind": "manual"}
        )
        assert converted.status_code == 200, converted.text
        assert converted.json()["document_count"] == 1

        assert (
            await client.put(
                f"/api/toolsets/{toolset_id}/documents/{document_id}",
                json={"path": "guides/refunds.md", "content": "# corrected"},
            )
        ).status_code == 200
        assert (
            await client.delete(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        ).status_code == 204


class TestDocumentsToolset:
    async def test_creating_a_documents_toolset_seeds_its_three_tools(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        customer_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", customer_id)
        await login(client, "admin@example.com")

        created = await client.post(
            "/api/toolsets", json={"name": "Handbook", "kind": "documents"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["kind"] == "documents"
        assert body["documents"] == []
        assert body["document_count"] == 0
        # `list_tools` orders by name, so the detail response is alphabetical
        # rather than in `DOCUMENT_TOOLS`' own order.
        assert [tool["name"] for tool in body["tools"]] == sorted(DOCUMENT_TOOL_NAMES)
        assert {tool["source"] for tool in body["tools"]} == {"documents"}
        assert {tool["mock_response"] for tool in body["tools"]} == {None}
        assert all(tool["enabled"] for tool in body["tools"])
        # No server, so nothing that names one may be left behind on it.
        assert body["mcp_url"] is None
        assert body["has_mcp_headers"] is False

        listed = (await client.get("/api/toolsets")).json()
        assert [(row["kind"], row["document_count"]) for row in listed] == [("documents", 0)]

    async def test_a_fourth_tool_cannot_be_hand_authored_but_enabled_still_flips(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Rewriting a description would make a retrieval result explainable by
        the wording; disabling `search_documents` is a *measurement*, so that
        stays allowed, and so does deleting a row that sync can put back.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        tools = {
            tool["name"]: tool["id"]
            for tool in (await client.get(f"/api/toolsets/{toolset_id}")).json()["tools"]
        }

        refused = await client.post(
            f"/api/toolsets/{toolset_id}/tools",
            json={"name": "lookup_order", "mock_response": "{}"},
        )
        assert refused.status_code == 400
        assert "documents toolset" in refused.json()["message"]

        rewritten = await client.put(
            f"/api/toolsets/{toolset_id}/tools/{tools['read_document']}",
            json={"name": "read_document", "description": "Reads whatever I say."},
        )
        assert rewritten.status_code == 400

        disabled = await client.put(
            f"/api/toolsets/{toolset_id}/tools/{tools['search_documents']}/enabled",
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert (
            await client.delete(f"/api/toolsets/{toolset_id}/tools/{tools['read_document']}")
        ).status_code == 204

    async def test_sync_restores_a_deleted_tool_and_never_re_enables_one(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """The invariant nothing downstream may "fix": a sync that helpfully
        switched `search_documents` back on would silently destroy the test case
        that asks whether a model can navigate by list+read alone.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        tools = {
            tool["name"]: tool["id"]
            for tool in (await client.get(f"/api/toolsets/{toolset_id}")).json()["tools"]
        }
        await client.put(
            f"/api/toolsets/{toolset_id}/tools/{tools['search_documents']}/enabled",
            json={"enabled": False},
        )
        await client.delete(f"/api/toolsets/{toolset_id}/tools/{tools['read_document']}")

        synced = await client.post(f"/api/toolsets/{toolset_id}/documents/sync")
        assert synced.status_code == 200, synced.text
        assert synced.json() == {
            "created": 1,
            "refreshed": 2,
            "tools": list(DOCUMENT_TOOL_NAMES),
        }

        detail = (await client.get(f"/api/toolsets/{toolset_id}")).json()
        by_name = {tool["name"]: tool for tool in detail["tools"]}
        assert sorted(by_name) == sorted(DOCUMENT_TOOL_NAMES)
        assert by_name["search_documents"]["enabled"] is False
        assert by_name["read_document"]["enabled"] is True
        assert by_name["read_document"]["source"] == "documents"


class TestDocumentUpload:
    async def test_one_multipart_request_accepts_the_markdown_and_names_every_refusal(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Per-file rejections, and the request still succeeds.

        A folder drop is the ordinary way a corpus arrives, so one `.txt` among
        thirty guides must not cost the other twenty-nine — which is why every
        file gets a row instead of the request getting a status.
        """
        toolset_id = await _signed_in_corpus(client, session, create_workspace)

        response = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload",
            files=[
                _upload("guides/refunds.md", REFUNDS.encode("utf-8")),
                _upload("guides/versand.markdown", b"# Versand\n\nDHL.\n"),
                _upload("notes.txt", b"# not markdown"),
                # Valid latin-1, invalid UTF-8: the one thing no fallback may
                # guess at, since mojibake in a corpus reads as the *model*
                # misquoting the documentation.
                _upload("latin.md", "# Café".encode("latin-1")),
                _upload("huge.md", b"#" + b"a" * (1024 * 1024)),
                _upload("blank.md", b"   \n  \n"),
            ],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["created"], body["replaced"], body["failed"]) == (2, 0, 4)

        outcome = {row["filename"]: row for row in body["results"]}
        assert outcome["guides/refunds.md"]["ok"] is True
        assert outcome["guides/refunds.md"]["path"] == "guides/refunds.md"
        assert outcome["guides/refunds.md"]["created"] is True
        assert outcome["notes.txt"]["error"] == (
            "Only markdown files can be uploaded (.md, .markdown)."
        )
        assert outcome["latin.md"]["error"] == (
            "Not valid UTF-8 text — re-save the file as UTF-8 markdown."
        )
        assert outcome["huge.md"]["error"] == "Larger than the 1024 kB per-document limit."
        assert outcome["blank.md"]["error"] == "The file is empty."

        # The response carries the whole corpus after the upload, so one call
        # refreshes the table.
        assert [row["path"] for row in body["documents"]] == [
            "guides/refunds.md",
            "guides/versand.markdown",
        ]
        # The folder in the filename survived as the corpus key, and the title
        # came from the markdown.
        async with async_session() as fresh:
            stored = (
                await fresh.scalars(
                    select(Document).where(Document.path == "guides/refunds.md")
                )
            ).all()
            assert len(stored) == 1
            assert stored[0].title == "Rückgaberichtlinie"
            assert stored[0].content == REFUNDS

    async def test_re_uploading_a_path_replaces_rather_than_conflicting(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        first = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload",
            files=[_upload("guides/refunds.md", b"# Refunds\n\nOld text.\n")],
        )
        assert first.json()["created"] == 1

        again = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload",
            files=[_upload("guides/refunds.md", b"# Refunds\n\nCorrected text.\n")],
        )
        assert again.status_code == 200, again.text
        body = again.json()
        assert (body["created"], body["replaced"], body["failed"]) == (0, 1, 0)
        assert body["results"][0]["created"] is False
        assert [row["path"] for row in body["documents"]] == ["guides/refunds.md"]

        document_id = body["documents"][0]["id"]
        detail = await client.get(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        assert detail.json()["content"] == "# Refunds\n\nCorrected text.\n"

    async def test_a_bom_is_stripped_so_the_first_heading_survives(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        response = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload",
            files=[_upload("refunds.md", "\ufeff# Refunds\n\nBody.\n".encode())],
        )
        assert response.status_code == 200, response.text
        assert response.json()["documents"][0]["title"] == "Refunds"

        async with async_session() as fresh:
            stored = (await fresh.scalars(select(Document))).all()
            assert stored[0].content == "# Refunds\n\nBody.\n"

    async def test_no_files_field_at_all_is_a_422(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        toolset_id = await _signed_in_corpus(client, session, create_workspace)
        response = await client.post(
            f"/api/toolsets/{toolset_id}/documents/upload", data={"nothing": "here"}
        )
        assert response.status_code == 422


class TestBorrowedCorpus:
    async def test_a_shared_corpus_reads_everywhere_and_writes_only_in_base(
        self, client: AsyncClient, session: AsyncSession, create_workspace: CreateWorkspace
    ) -> None:
        """Reading a borrowed corpus — `content` included — is deliberate: it is
        exactly what a retrieval measurement is measured against, and the same
        documents the executor reads live for a run in this workspace.

        Every write is a named 403 rather than the silent no-op `scope_where`
        alone would give, and it is refused for an `admin` here, so the refusal
        is about ownership and not about a role.
        """
        _, base = await _make_base(session, create_workspace)
        toolset = await create_toolset(
            base, session, name="Shared handbook", kind="documents", is_global=True
        )
        toolset_id = toolset.id
        document = await create_document(
            base, session, toolset_id, title="Refunds", path="guides/refunds.md", content=REFUNDS
        )
        document_id = document.id
        other_id, _ = await create_workspace("Acme")
        await session.commit()
        await make_user(session, "admin@example.com", "admin", other_id)
        await login(client, "admin@example.com")

        listed = (await client.get("/api/toolsets")).json()
        assert [
            (row["id"], row["is_global"], row["editable"], row["document_count"])
            for row in listed
        ] == [(toolset_id, True, False, 1)]

        corpus = await client.get(f"/api/toolsets/{toolset_id}/documents")
        assert corpus.status_code == 200
        assert [row["path"] for row in corpus.json()] == ["guides/refunds.md"]
        read = await client.get(f"/api/toolsets/{toolset_id}/documents/{document_id}")
        assert read.status_code == 200
        assert read.json()["content"] == REFUNDS

        for response in (
            await client.post(
                f"/api/toolsets/{toolset_id}/documents",
                json={"path": "mine.md", "content": "# mine"},
            ),
            await client.put(
                f"/api/toolsets/{toolset_id}/documents/{document_id}",
                json={"path": "guides/refunds.md", "content": "# hijacked"},
            ),
            await client.delete(f"/api/toolsets/{toolset_id}/documents/{document_id}"),
            await client.post(
                f"/api/toolsets/{toolset_id}/documents/upload",
                files=[_upload("mine.md", b"# mine")],
            ),
            await client.post(f"/api/toolsets/{toolset_id}/documents/sync"),
        ):
            assert response.status_code == 403, response.text
            assert "Base workspace" in response.json()["message"]

        # Nothing was written by any of them.
        async with async_session() as fresh:
            stored = (await fresh.scalars(select(Document))).all()
            assert [(row.path, row.content) for row in stored] == [
                ("guides/refunds.md", REFUNDS)
            ]
