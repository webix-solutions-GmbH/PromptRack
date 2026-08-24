<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="frontend/public/logo-dark.png" />
    <img src="frontend/public/logo.png" alt="PromptRack" width="80" />
  </picture>
</p>

<h1 align="center">PromptRack</h1>

<p align="center">
  Git for your customers' prompts, plus the evidence that they still work.<br/>
</p>

---

Keep the system prompts behind the AI solutions you sold in one versioned library, then
prove which model runs them well enough — invoice intake, document extraction, an agent
over a company's RAG — and on what hardware. Any OpenAI-compatible endpoint, local or
hosted, in one matrix.

## Features

| | |
| --- | --- |
| **Prompt library** | Every customer's system prompts in one place, one library per engagement. A prompt is a mutable draft plus an immutable commit history — commit with a message, diff any two versions, restore an old one. |
| **Deployed marker** | One version per prompt is flagged as live at the customer. When it is no longer the newest, the app says so — "deployed v3, head is v5" is the one-glance answer to whether production is what you last verified. |
| **Regression checks** | A version can name a `baseline` run: the measurement that justified deploying it. Verify replays those test cases against a new model and puts the two runs side by side. That is the model-upgrade decision, made on evidence. |
| **Model fitness** | Test cases are the customer's real work, not a leaderboard. Rate each answer good / meh / bad against the expected output; the verdict is per job, not in general. |
| **Hardware sizing** | Every result names the machine that produced it, with TTFT, duration, tokens and tok/s. If a small model does the job, a Mac Mini may be enough — but it has to be measured. |
| **Agents, not just chat** | A test case can offer tools and really execute them, looping until the model answers, so an invoice agent is evaluated as the agent it will be. |
| **MCP server** | `POST /mcp` — an agent pushes prompts, test cases and runs in from outside and reads the measurements back. The interesting test cases already exist in the repo that defines the job. |

## What it can test

Each step adds one thing to the one above it.

| | |
| --- | --- |
| **Test case** | Can the model do the task at all? |
| **+ expected output** | Graded against a rubric instead of a vibe. |
| **+ tool definitions** | Does it pick the right tool, with the right arguments? Nothing executes. |
| **+ mocked tools** | The full multi-turn loop. Canned responses, identical for every model, so no ERP or RAG index has to exist. |
| **+ live MCP tools** | The same loop against the customer's real stack. |

**Example.** A supplier invoice says 10 units; the purchase order says 8. The model gets
one tool, `lookup_purchase_order`.

The test case alone tells you whether it reconciles the two numbers at all. Add the mocked
tool and you see whether it looks the PO up *before* deciding, and whether it stops and asks
rather than booking a wrong amount — with the same canned response for every model, so the
difference you measure is the model. Point that toolset at the customer's MCP server and
the identical test runs against their ERP.

A worked suite of 38 test cases is written up in
[docs/example-suite](docs/example-suite/) — invoice intake, an invoice agent, general
capability, and prompt injection through the tool-result and tool-description channels.
Point your agent at it and tell it to set the same thing up for your customer.

## Quick start

```bash
cp .env.example .env    # dev defaults work as-is
make run                # postgres in docker, migrations, backend on :8077, frontend on :5177
```

`make` on its own lists the other targets (`test`, `lint`, `typecheck`, `check`).

The first account created becomes the administrator, and sign-up closes behind it; there
are no seeded users and no default password. Create a customer workspace next — everything
else belongs to one. Settings: [`.env.example`](.env.example). Production:
`docker login ghcr.io` (the image is private while the repo is), then `make docker-up` —
one container serving the API, the SPA and MCP.

## Concepts

| | |
| --- | --- |
| **Workspace** | One per customer engagement. Nothing crosses between them. |
| **Prompt** | The versioned asset: a draft, a history of committed versions, a deployed marker. |
| **Test case** | One job to be done, optionally with tools. Expected output is the rubric, never sent to the model. |
| **Machine** | An endpoint plus hardware notes. |
| **Run** | One test-group selection × one model × one machine. Everything it used is snapshotted, so later edits cannot rewrite history. |
| **Result** | Response, rating, TTFT, tok/s, duration, tokens, and the prompt version it tested. |
| **`/results`** | The matrix — by model (latest result per test case) or by run (two runs of one model side by side). |

tok/s measures decode only: TTFT and tool waits are excluded. A `~` means the endpoint sent
no usage data.

## MCP API

```bash
claude mcp add --transport http promptrack https://promptrack.example.com/mcp \
  --header "x-api-key: prk_…" --header "x-customer: Acme GmbH"
```

Per-user token, carrying its owner's role. Prompts, versions, test cases, runs and ratings
are writable. Machines, toolsets, workspaces and the deployed marker stay UI-only — they
are credentials, or a human's claim about a customer's production system.

Grading runs is agent work too: [docs/run-review-skill](docs/run-review-skill/) is a
Claude Code skill that reviews a finished run over this API — every unrated result graded
against its expected output with an evidence note, nothing guessed where no rubric exists,
human ratings never overwritten — and compares two graded runs, as a baseline regression
check or two models head to head. Copy the folder into `.claude/skills/promptrack-run-review/`
wherever the reviewing agent runs.

## Roles

| | |
| --- | --- |
| **Admin** | Members' rights plus machines, toolset credentials, workspace deletion. |
| **Member** | Prompts, versions, test cases, tools, runs, ratings. |
| **Viewer** | Read-only. |

Accounts beyond the first come from OIDC (`OIDC_ISSUER` and friends); there is no
user-management UI yet. Endpoint keys and MCP headers are stored in plaintext — treat the
database accordingly, and keep `ENABLE_MOCKS` unset in production.

[CLAUDE.md](CLAUDE.md) has the architecture: FastAPI + async SQLAlchemy on Postgres, a
Vue 3 + PrimeVue SPA, and the MCP server mounted in the same process.
