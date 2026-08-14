# Invoice Agent (Pipeline)

4 test cases.

How to create these: [README](README.md). Create the group with `create_test_group`, then one `create_test_case` per block below, in this order.

Group description (pass as `description` to `create_test_group`):

```text
Self-contained evals matching the real invoice-agent pipeline prompts (system prompts from FoundryAgentFactory.cs, user format from GptExecutors.cs): PO judge, reconcile approve/ask, reply interpreter.
```

Four evals lifted from a real invoice-agent pipeline (system prompts from `FoundryAgentFactory.cs`, user-message format from `GptExecutors.cs`). Each expects a rigid one-line-or-two-line output format that a parser downstream depends on.

**Three prompt assets serve the four cases.** A test case holds no prompt text of its own: each block below names a `system_prompt` by name, and every one of them is a `create_prompt` with `kind: "system"` — that is the channel these texts are sent on, and moving one to `kind: "task"` would put it in the user message instead. Create the prompt first, then the test case that references it.

Two of the four share one prompt — the production reconcile prompt, also used by Injection 15 in group 4. Create it once with `create_prompt` ([the text is in the README](README.md#step-2b-the-prompts)) and reference it from both.

---

## 1. Judge: ambiguous PO candidates

- `tool_mode`: `none`
- `system_prompt`: `Judge: ambiguous PO candidates`

That prompt is its own `create_prompt` (`kind: "system"`, name exactly as above), with this content:

```text
You match a supplier invoice to one purchase order from a candidate list. Vendor names
on invoices rarely match the ERP partner exactly (legal-entity suffixes, abbreviations),
and totals can differ by tax or rounding. Weigh vendor/partner similarity, total, and date.
Reply with ONLY the exact PurchaseOrder name (e.g. "P00012") of the single best match, or
the single word NONE if you cannot confidently identify one. No other text.
```

`content`:

```text
Invoice: vendor="Nordlicht Handels GmbH & Co. KG", total=4165.00 EUR, date=2026-06-12, PO-printed-on-invoice=none.
Purchase-order candidates:
- P00018: partner="Nordlicht Handel", total=4165.00 EUR, date=2026-06-05, state=purchase
- P00019: partner="Nordlicht Handel", total=4165.00 EUR, date=2026-04-02, state=purchase
- P00007: partner="Süddeutsche Bürotechnik AG", total=4165.00 EUR, date=2026-06-10, state=purchase
```

`expected_output`:

```text
P00018

Pass: output contains "P00018" and nothing contradicting it (ideally the bare PO name).
Fail: P00019 (stale date), P00007 (wrong partner despite matching total), NONE, or any
explanation text around the answer (the parser does a substring match, so extra prose is
tolerated at runtime — but a well-behaved model returns only the name).
```

---

## 2. Reconcile: folded discount → APPROVE

- `tool_mode`: `none`
- `system_prompt`: `Reconcile invoice ↔ PO (pipeline)` (the shared prompt — created once, see the [README](README.md#step-2b-the-prompts))

`content`:

```text
Invoice: vendor="Süddeutsche Bürotechnik AG", total=2617.30 EUR, date=2026-06-20.
Invoice positions:
- Dokumentenscanner DS-940: qty=5, amount=2450.00
- Wartungspauschale Juni: qty=1, amount=167.30
Matched purchase order P00021: total=2617.30 EUR.
PO positions:
- Dokumentenscanner DS-940: qty=5, unit_price=515.00, total=2575.00
- Wartungspauschale: qty=1, unit_price=167.30, total=167.30
- Aktionsrabatt: qty=1, unit_price=-125.00, total=-125.00
Why it needs review: totals match but line counts differ (2 vs 3)
```

`expected_output`:

```text
APPROVE | <one short reason>
e.g. APPROVE | Totals match; scanner line difference is the PO's itemized Aktionsrabatt folded into the invoice price.

Pass: first token is APPROVE, followed by | and a reason.
Fail: ASK (asking about the Rabatt line or the 2450 vs 2575 scanner price — that gap is
exactly the -125.00 discount), or any output not starting with APPROVE.
```

---

## 3. Reconcile: quantity mismatch → ASK

- `tool_mode`: `none`
- `system_prompt`: `Reconcile invoice ↔ PO (pipeline)` (the shared prompt — created once, see the [README](README.md#step-2b-the-prompts))

`content`:

```text
Invoice: vendor="Nordlicht Handels GmbH & Co. KG", total=5220.00 EUR, date=2026-06-25.
Invoice positions:
- Höhenverstellbarer Schreibtisch E5: qty=12, amount=5220.00
Matched purchase order P00018: total=4350.00 EUR.
PO positions:
- Höhenverstellbarer Schreibtisch E5: qty=10, unit_price=435.00, total=4350.00
Why it needs review: totals differ (5220.00 vs 4350.00)
```

`expected_output`:

```text
First line exactly "ASK", then a short German note using "du", no salutation/sign-off, e.g.:

ASK
Die Rechnung von Nordlicht listet 12 Schreibtische E5 (5.220,00 EUR), bestellt waren laut P00018 aber nur 10 (4.350,00 EUR). Wurden tatsächlich 12 geliefert, oder stimmt die Rechnung nicht?

Pass: first line is ASK; body is German, informal "du", mentions the 12-vs-10 quantity
(or the 870 EUR gap) exactly once, ends without signature.
Fail: APPROVE (20% gap is far outside tolerance), English body, "Sehr geehrte…", or
restating the same discrepancy as both a quantity and a total difference.
```

---

## 4. Reply interpreter: nachgebucht → RECHECK

- `tool_mode`: `none`
- `system_prompt`: `Reply interpreter: nachgebucht → RECHECK`

That prompt is its own `create_prompt` (`kind: "system"`, name exactly as above), with this content:

```text
You read an orderer's free-text reply (usually German) to a clarification email about an
invoice, and decide the outcome — one of three:
- The orderer says they have just BOOKED / POSTED / CREATED or CORRECTED the order in the ERP
  (Odoo) and want you to look again (e.g. "habe ich nachgebucht, schau nochmal", "ist jetzt
  angelegt", "habe es korrigiert") → RECHECK. We will re-query the ERP.
- The reply confirms the invoice is legitimate / identifies a purchase order → APPROVED.
- The reply rejects the invoice or is unclear / non-committal → FLAGGED for manual handling.
Reply on a single line in EXACTLY one of these formats:
  RECHECK  | <one short note>
  APPROVED | <po-name-or-"none"> | <one short reason>
  FLAGGED  | none | <one short reason>
Prefer RECHECK whenever the orderer claims they changed something in the ERP and asks to
re-check, even if they also sound confident it is fine now.
```

`content`:

```text
Schriftverkehr mit dem Besteller zu dieser Rechnung (chronologisch):
---
Agent fragt:
Die Rechnung von Nordlicht Handels GmbH & Co. KG über 4.165,00 EUR vom 12.06.2026 passt zu keiner offenen Bestellung. Kannst du sagen, zu welcher Bestellung sie gehört?

Besteller antwortet:
Sorry, das war mein Fehler — die Bestellung hatte ich nur als Entwurf gespeichert. Habe sie eben in Odoo nachgebucht (P00023), sollte jetzt alles passen. Schau bitte nochmal.
---
Interpretiere die LETZTE Antwort des Bestellers im Kontext des gesamten Verlaufs.
```

`expected_output`:

```text
RECHECK | <one short note>
e.g. RECHECK | Besteller hat P00023 in Odoo nachgebucht

Pass: single line starting with RECHECK and containing a |.
Fail: APPROVED | P00023 | ... — the classic miss: the model latches onto the named PO and
the confident tone instead of the "nachgebucht, schau nochmal" trigger.
```
