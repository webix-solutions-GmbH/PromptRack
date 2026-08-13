# How it works

**Workspaces** — one per customer engagement, which is the whole reason they exist: one
customer's endpoints, API keys, prompts and history stay out of another's. Deleting one
refuses while it still holds anything, so a delete can never take run history with it.
Archiving is the soft path.

**Machines and models** — a machine is an endpoint (base URL, optional API key) plus
hardware notes. "Discover models" reads `/v1/models`, so a run can only pick a model known
to be served there. One model on two machines stays two columns: throughput belongs to the
hardware, and a hosted API is simply a machine whose notes say so.

**Prompts** — grouped, one group per job to be done. Each carries the user message, the
system prompt the production agent would use, and optionally the expected output — which is
never sent to the model. It is the rubric a rater grades against.

**Tool tests** — a prompt selects any number of toolsets and picks a mode: offer the tools
and only record which calls the model wanted, or execute the calls and feed the results
back until it answers or hits `max_turns`. A toolset is **manual** (tools written in the UI
answering with a canned response) or **MCP** (discovered from a streamable-HTTP server and
really executed). A failing tool call is fed back to the model as tool output rather than
failing the row: that is what a real agent sees, and how it recovers is the measurement.

**Runs** — every prompt of the selected groups against one model on one machine, streamed,
recording TTFT, tok/s, duration and token counts per result. Prompt text, system prompt,
tool definitions and the machine are snapshotted into the run, so editing or deleting any
of them later cannot falsify history. Each answer is rated good / meh / bad with a note;
`meh` usually means the *prompt* needs work rather than the model. Closing the tab stops a
run, and Resume picks up the prompts it never reached.

**Results** — a prompt × model matrix with two pivots, both encoded in the URL so a view
can be bookmarked. *By model* takes the live prompts as rows and fills each cell with that
model's most recent usable result from any run; one model is a complete selection, which is
how you review a single model across everything it has answered. *By run* is the only pivot
that can place two runs of the same model side by side — a quantization swap, a temperature
A/B, a prompt rewrite. Where a row's cells come from runs made under different conditions,
the row header says so, so a configuration difference is never read as a model difference.

## Metrics

- **TTFT** — request to first token. Mostly prefill and queueing, so it is what suffers on
  long documents.
- **tok/s** — completion tokens over the generation window *after* the first token. On a
  multi-turn tool run the denominator sums each turn's own window, so time spent waiting on
  a tool is never counted as generation.
- **duration** — wall clock for the whole request.
- **~estimated** — the endpoint returned no usage block, so the token count was estimated
  from response length (~4 characters per token), and tok/s with it.

## Seeding

`npm run db:seed` fills one workspace — the oldest, or `SEED_CUSTOMER` by name or id — with
a German invoice workflow, a multi-step invoice agent, a general capability set and the
injection suite. It is additive and remembers deletions per workspace, so re-running it is
safe and so is seeding the next customer. A `SEED_CUSTOMER` matching nothing exits non-zero
and lists what exists, rather than creating a workspace off a typo.
