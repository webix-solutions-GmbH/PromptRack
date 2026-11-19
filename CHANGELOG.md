# Changelog

Newest first, one line per change. A version heading is the git tag, which is
also that build's container image tag — `0.3.0` here is
`ghcr.io/webix-solutions-gmbh/promptrack:0.3.0`, and the GitHub release on the
same tag carries this section verbatim.

## Unreleased

- Manual (mock) toolsets and their tools can be listed, created, updated and deleted over MCP
- Members can create and edit manual toolsets; MCP-server toolsets, sharing and deleting stay admin
- Tool descriptions and mock responses take long texts (50k+ characters) and get a multi-line editor
- A manual tool can hold several mock responses, answered by call order — the last one repeats
- A test case can override a manual tool's mock response, so one shared toolset serves many cases
- A test case can carry earlier user/assistant turns, sent before its own message for follow-up questions
- Fixed MCP text arguments that look like JSON (a JSON document as test-case content, the word "null") being refused or turned into nothing
- A second toolset with a name already in use is refused in the UI too, not only over MCP

## 0.4.0 — 2026-08-31

- Parameter groups: named request-param presets ("no thinking") selectable on any run, merged between the endpoint's defaults and the run's own params

## 0.3.1 — 2026-08-26

- Added "Total time" to the runs overview — summed generation time, the same measurement `/results` shows
- Show a run's note as readable text in the `/results` run picker instead of a hover bubble
- Added this changelog, and cut a GitHub release from the same tag that publishes the image
- Gate the published image on `make check`, so a red suite no longer produces a `latest`
- The sidenav build string links to the commit it was built from, or to its release
- Fixed the `PROMPTRACK_TAG` example, which suggested a `v` prefix the published image tags don't carry

## 0.3.0 — 2026-08-24

- `get_run` can drop the rubrics from a grading work list
- Rewrote the README around the two co-equal angles: model fitness and hardware sizing
- Documented the run-review skill and the copy of it that lives in the AI Companion

## 0.2.0 — 2026-08-24

- Id-addressed MCP calls (`get_run`, `set_rating`, …) resolve the workspace from the row, so no `customer` argument is needed
- Added a run-review skill agents grade a finished run with over MCP
- Added the `documents` toolset kind: markdown corpora a model retrieves from through three real tools
- Reasoning models are measured on both clocks — thinking counts as generation, and `ttft_content_ms` keeps the visible-token latency
- Show a model's thinking whichever channel it arrived on (`<think>` tags or `reasoning_content`)
- Free-form request params, per-platform catalogs and per-endpoint defaults
- `/results` renders a single selected run, and names each run's params and comment in its column header
- Filter the `/results` matrix down to the meh and bad rows
- Maximise the `/results` matrix into the window
- Paginated the run and model pickers
- Show a rating note inline instead of hiding it behind a pen
- Record rating provenance (human vs. agent) and surface `tools_called` for grading loops
- Added an admin Users page with invites, deactivation and OIDC status
- Added an MCP settings page at `/mcp`
- Moved Docker under `docker/` and publish the image to GHCR
