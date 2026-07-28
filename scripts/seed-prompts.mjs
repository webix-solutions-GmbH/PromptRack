#!/usr/bin/env node
/**
 * Seeds ready-to-run benchmark toolsets and prompt groups.
 *
 * Like `init-db.mjs` this depends on nothing but `better-sqlite3`, so it can
 * run inside the standalone container image (`node scripts/seed-prompts.mjs`)
 * as well as in development (`npm run db:seed`).
 *
 * Idempotency is per object and recorded in `__app_seeds`, so the script is
 * additive *and* respects deletions: every toolset/prompt is seeded at most
 * once ever, which means new seed entries land in groups an earlier version
 * created, while anything you deleted afterwards stays deleted. A database that
 * predates the ledger is backfilled on first run from what is already there.
 * (`__app_seeds` is owned by this script, in the same spirit as
 * `__app_migrations` in `init-db.mjs`; neither belongs in `src/db/schema.ts`.)
 *
 * Prompts deliberately embed all instructions and data inline (no
 * `system_prompts` dependency), so each one is self-contained. A prompt may
 * carry its own `systemText`, stored as a per-prompt override system prompt,
 * and may reference seeded toolsets by name via `toolsets`.
 */
import path from 'node:path';
import Database from 'better-sqlite3';

const root = process.env.APP_ROOT ?? process.cwd();
const dataDir = process.env.DATA_DIR ?? path.join(root, 'data');
const dbPath = path.join(dataDir, 'app.db');

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

/**
 * Manual toolsets: the tools are defined here and answer with a fixed canned
 * response, so a tool test is fully deterministic and comparable across models
 * without touching any real system.
 *
 * The canned responses are deliberately written to stay *correct whatever
 * arguments the model passes* — `convert_currency` returns a rate rather than a
 * converted amount, for instance, so the model still has to do the arithmetic
 * and the response never contradicts the call.
 */
const TOOLSETS = [
  {
    name: 'Demo Utilities (mock)',
    description:
      'Canned weather / catalogue / currency / stock lookups for tool-calling tests. No real system is contacted.',
    tools: [
      {
        name: 'get_weather',
        description: 'Current weather observation for a city.',
        parameters: {
          type: 'object',
          properties: {
            city: { type: 'string', description: 'City name, e.g. "Berlin"' },
            unit: {
              type: 'string',
              enum: ['celsius', 'fahrenheit'],
              description: 'Unit for the returned temperature. Defaults to celsius.',
            },
          },
          required: ['city'],
        },
        mockResponse: {
          city: 'Berlin',
          observed_at: '2026-07-27T11:00:00Z',
          temperature_celsius: 21.4,
          condition: 'partly cloudy',
          wind_kph: 14,
          humidity_percent: 58,
        },
      },
      {
        name: 'search_products',
        description: 'Search the product catalogue. Prices are in euros.',
        parameters: {
          type: 'object',
          properties: {
            query: { type: 'string', description: 'Free-text product search' },
            max_price: { type: 'number', description: 'Optional upper price bound in EUR' },
          },
          required: ['query'],
        },
        mockResponse: {
          currency: 'EUR',
          results: [
            { sku: 'LAP-001', name: 'ThinkPad T14 Gen 5', price: 1049.0, in_stock: true },
            { sku: 'LAP-002', name: 'Dell Latitude 5450', price: 890.0, in_stock: true },
            { sku: 'LAP-003', name: 'HP EliteBook 645 G11', price: 979.0, in_stock: false },
          ],
        },
      },
      {
        name: 'convert_currency',
        description:
          'Exchange rate between two ISO 4217 currencies. Returns the rate; the caller applies it.',
        parameters: {
          type: 'object',
          properties: {
            from_currency: { type: 'string', description: 'ISO 4217 code, e.g. "EUR"' },
            to_currency: { type: 'string', description: 'ISO 4217 code, e.g. "USD"' },
          },
          required: ['from_currency', 'to_currency'],
        },
        mockResponse: {
          from_currency: 'EUR',
          to_currency: 'USD',
          rate: 1.084,
          as_of: '2026-07-27',
          note: 'Multiply the EUR amount by rate to get USD.',
        },
      },
      {
        name: 'get_stock_level',
        description: 'Warehouse stock level for one SKU.',
        parameters: {
          type: 'object',
          properties: {
            sku: { type: 'string', description: 'Product SKU, e.g. "LAP-002"' },
          },
          required: ['sku'],
        },
        mockResponse: { sku: 'LAP-002', on_hand: 17, reserved: 3, warehouse: 'WH-1' },
      },
    ],
  },
];

// Shared by the two reconcile evals below — mirrors the real pipeline prompt
// from FoundryAgentFactory.cs.
const RECONCILE_SYSTEM = `You reconcile a supplier invoice against the ONE purchase order it was matched to. You get the
invoice header + positions and the PO header + positions. Reason like an accountant.

The decisive question is whether the invoice and PO describe the SAME purchase for the SAME
money — NOT whether their position lists are formatted identically. The two sides routinely
itemize differently, and that alone is never a problem:
- An invoice often folds discounts, shipping, or fees into its TOTAL (or into a line's price)
  instead of listing them, while the PO itemizes them as separate positions — e.g. "Rabatt",
  "Aktionsrabatt", "Skonto", "Versand", "Versandkosten". A discount / shipping / fee position
  that appears on only ONE side is NOT a difference to ask about when the header totals still
  reconcile: it is exactly what explains the gap between a line and the total.
- Descriptions may be worded differently; tax, rounding (a few cents), wording and ordering
  never matter.

Procedure:
1. Compare the header totals FIRST. If the invoice total ≈ the PO total (within ~1%), the
   purchase amount agrees — this is the strongest signal and on its own normally means approve.
2. Map the real GOODS / SERVICE positions to each other. Any position difference that is fully
   explained by a one-sided discount / shipping / fee line, by tax, or by rounding is NOT a root
   difference.
3. A ROOT difference is a genuinely UNEXPLAINED gap: a real product/service on one side that is
   missing from the other, a wrong quantity of goods, or a materially wrong price that the
   totals do NOT absorb.

Then decide:
- Totals reconcile and every position difference is explained (discount / shipping / fee / tax /
  rounding) → reply EXACTLY:
    APPROVE | <one short reason>
- There is a genuine, unexplained root difference → reply with a first line containing only
    ASK
  then a SHORT German note to the orderer: informal "du", no salutation, no sign-off, no
  signature. State each root difference ONCE and ask whether it is correct. Never restate the
  same discrepancy as both a position and a total.
Reply with nothing else.`;

const GROUPS = [
  {
    name: 'General Capabilities',
    description:
      'Broad capability tests: reasoning, extraction, formatting, code. Language: English.',
    prompts: [
      {
        title: 'Three-bullet summary',
        content: `Summarize the following text in exactly 3 bullet points. Each bullet point must be at most 15 words. Output only the bullet points, nothing else.

Text:
The shipping container, standardized in the 1960s, transformed global trade more than almost any other invention of the twentieth century. Before containerization, cargo was loaded piece by piece by large gangs of dockworkers, a slow and expensive process during which goods were frequently damaged or stolen. A single ship could spend more time in port than at sea. The standardized steel box changed the economics completely: cranes could move containers directly between ships, trucks, and trains without touching the contents, cutting loading costs by more than 90 percent and port time from weeks to hours. As a result, manufacturers could suddenly source components from anywhere in the world at low cost, which enabled the global supply chains we know today. Ports that invested early in container terminals, such as Rotterdam and Singapore, grew into major trade hubs, while many traditional harbor districts declined.`,
        expectedOutput: `Rating aid — the three bullets should cover roughly these points, each within 15 words:
- Containerization (1960s) replaced slow, costly piece-by-piece cargo loading.
- Standardized boxes cut loading costs over 90% and port time from weeks to hours.
- Cheap transport enabled global supply chains; early container ports became major hubs.
Check: exactly 3 bullets, no intro/outro text, each bullet has at most 15 words.`,
      },
      {
        title: 'JSON extraction',
        content: `Extract the order data from the confirmation below. Output only valid JSON, no markdown code fences, no explanation. Use exactly these keys: customer_name, order_number, order_date (ISO 8601), items (array of objects with name, quantity, unit_price), total.

Order confirmation:
Dear Emily Carter, thank you for your order #ORD-2024-1187 placed on March 5, 2024. We hereby confirm the following items: 2x Ergonomic Desk Chair at $249.00 each, 1x Standing Desk Frame at $399.00, and 3x Monitor Arm at $59.00 each. The total amount of your order is $1,074.00 and will be charged to your card on file.`,
        expectedOutput: `{
  "customer_name": "Emily Carter",
  "order_number": "ORD-2024-1187",
  "order_date": "2024-03-05",
  "items": [
    { "name": "Ergonomic Desk Chair", "quantity": 2, "unit_price": 249.00 },
    { "name": "Standing Desk Frame", "quantity": 1, "unit_price": 399.00 },
    { "name": "Monitor Arm", "quantity": 3, "unit_price": 59.00 }
  ],
  "total": 1074.00
}
Check: valid JSON, no code fences, exact key names, numbers as numbers (not strings).`,
      },
      {
        title: 'Multi-step logic',
        content: `Anna, Ben, Clara, and David sit in a row of four seats numbered 1 to 4 from left to right.

- Anna does not sit at either end.
- Ben sits somewhere to the left of Anna.
- Clara sits immediately to the right of Anna.
- David sits at seat 4.

Who sits in which seat? Answer in one sentence.`,
        expectedOutput: `Ben sits in seat 1, Anna in seat 2, Clara in seat 3, David in seat 4.
(Unique solution: Anna must be in 2 or 3; Anna in 3 would put Clara in 4, conflicting with David, so Anna is in 2.)`,
      },
      {
        title: 'Arithmetic word problem',
        content: `A laptop has a list price of $1,200. During a sale it is discounted by 15%. A loyal customer receives an additional 10% off the sale price. Finally, 8% sales tax is applied to the discounted price. How much does the customer pay in total? Show your calculation steps and give the final amount rounded to the cent.`,
        expectedOutput: `$991.44
(1200 x 0.85 = 1020; 1020 x 0.90 = 918; 918 x 1.08 = 991.44)`,
      },
      {
        title: 'Strict instruction following',
        content: `What is the capital of France? Respond with exactly five words, all lowercase, no punctuation.`,
        expectedOutput: `Checklist: exactly 5 words / everything lowercase / no punctuation at all / contains "paris".
Example of a valid answer: "the capital city is paris"`,
      },
      {
        title: 'Code generation',
        content: `Write a Python function parse_duration(s) that converts a duration string such as "1h 30m 15s" into the total number of seconds as an int.

Requirements:
- The parts hours (h), minutes (m), and seconds (s) may each be present or absent, but always appear in that order, separated by single spaces.
- Examples: "1h 30m 15s" -> 5415, "90m" -> 5400, "2h" -> 7200, "45s" -> 45.
- Raise a ValueError for invalid input (empty string, unknown units, wrong order).
- Include a docstring with doctests covering the examples above.

Output only the code.`,
        expectedOutput: `Behavioral contract to check:
- parse_duration("1h 30m 15s") == 5415
- parse_duration("90m") == 5400
- parse_duration("2h") == 7200
- parse_duration("45s") == 45
- ValueError on "" and on invalid strings like "abc" or "30m 1h"
- Docstring with doctests present; output is code only.`,
      },
      {
        title: 'Bug finding',
        content: `The following Python function is supposed to return the moving average over every consecutive window of the given size, but it has a bug. Identify the bug, explain it in one or two sentences, and provide the corrected line.

def moving_average(values, window):
    """Return the average of each consecutive 'window' values."""
    result = []
    for i in range(len(values) - window):
        chunk = values[i:i + window]
        result.append(sum(chunk) / window)
    return result`,
        expectedOutput: `Off-by-one in the range: the last window is skipped. For example moving_average([1, 2, 3], 2) returns only [1.5] instead of [1.5, 2.5].
Corrected line: for i in range(len(values) - window + 1):`,
      },
      {
        title: 'Long-form generation',
        content: `Explain how DNS (the Domain Name System) works to a junior developer in about 300 words. Structure your answer with a short introduction, three sections with headings (the resolution steps, caching, and common record types), and a one-sentence summary at the end.`,
        expectedOutput: null,
      },
      {
        title: 'Tools: pick the right one',
        toolMode: 'definitions',
        toolsets: ['Demo Utilities (mock)'],
        content: `What is the current temperature in Berlin, in degrees Celsius?`,
        expectedOutput: `Exactly one tool call: get_weather with city="Berlin".
Passing "unit": "celsius" is fine (and slightly better); omitting unit is also correct since celsius is the documented default.

Fail: calling search_products / convert_currency / get_stock_level, calling more than one tool, inventing a tool that was not offered, a wrong or empty city, or answering from memory with a made-up temperature instead of calling anything.
Note: this prompt runs in "definitions" mode — nothing is executed, so judge the call itself, not any answer.`,
      },
      {
        title: 'Tools: chain two calls and do the math',
        toolMode: 'execute',
        toolsets: ['Demo Utilities (mock)'],
        maxTurns: 6,
        content: `Our catalogue is priced in euros. Using the available tools, find the cheapest laptop we sell and tell me what it costs in US dollars. State the product name and the final USD amount rounded to the cent.`,
        expectedOutput: `Dell Latitude 5450 — $964.76

Required behaviour:
- calls search_products (query about laptops), then convert_currency (EUR -> USD)
- identifies 890.00 EUR (Dell Latitude 5450) as the cheapest of the three results
- applies the returned rate itself: 890.00 x 1.084 = 964.76 exactly

Fail: picking the 1049.00 or 979.00 item, only calling one of the two tools, inventing an exchange rate instead of calling convert_currency, or arithmetic that is off by more than a cent.`,
      },
      {
        title: 'Tools: restraint — no call needed',
        toolMode: 'definitions',
        toolsets: ['Demo Utilities (mock)'],
        content: `How many continents are there on Earth? Answer in one short sentence.`,
        expectedOutput: `No tool call at all, plus a direct answer naming seven continents.

This tests restraint: none of the offered tools (weather, product search, currency, stock) can answer a general-knowledge question, so a well-behaved model answers directly.
Fail: any tool call — the classic over-eager failure is calling search_products with "continents" because tools were offered at all. The result card shows "0 tool calls" and stop reason "the model answered" when it passes.`,
      },
    ],
  },
  {
    name: 'Rechnungsworkflow (DE)',
    description:
      'Deutsche Prompts für einen Kreditoren-Workflow: Extraktion, ERP-Abgleich, Prüfung, Freigabe.',
    prompts: [
      {
        title: 'Rechnungsdaten extrahieren',
        content: `Extrahiere die Daten aus der folgenden Rechnung. Gib ausschließlich gültiges JSON aus, ohne Markdown-Codeblöcke und ohne Erläuterung. Verwende genau diese Schlüssel: rechnungsnummer, datum (ISO 8601), lieferant, ust_id, positionen (Array aus Objekten mit bezeichnung, menge, einzelpreis, gesamtpreis), netto, ust, brutto. Beträge als Zahlen mit Punkt als Dezimaltrennzeichen.

Rechnung:
Rechnung Nr. RE-2025-0442
Datum: 12.03.2025
Lieferant: Müller Bürotechnik GmbH, Hauptstraße 12, 50667 Köln
USt-IdNr.: DE812345678

Pos. 1: 4 x Laserdrucker-Toner schwarz, Einzelpreis 89,50 EUR, Gesamt 358,00 EUR
Pos. 2: 10 x Kopierpapier A4 (500 Blatt), Einzelpreis 4,20 EUR, Gesamt 42,00 EUR
Pos. 3: 2 x USB-C-Dockingstation, Einzelpreis 149,00 EUR, Gesamt 298,00 EUR

Nettobetrag: 698,00 EUR
zzgl. 19 % USt: 132,62 EUR
Rechnungsbetrag: 830,62 EUR`,
        expectedOutput: `{
  "rechnungsnummer": "RE-2025-0442",
  "datum": "2025-03-12",
  "lieferant": "Müller Bürotechnik GmbH",
  "ust_id": "DE812345678",
  "positionen": [
    { "bezeichnung": "Laserdrucker-Toner schwarz", "menge": 4, "einzelpreis": 89.50, "gesamtpreis": 358.00 },
    { "bezeichnung": "Kopierpapier A4 (500 Blatt)", "menge": 10, "einzelpreis": 4.20, "gesamtpreis": 42.00 },
    { "bezeichnung": "USB-C-Dockingstation", "menge": 2, "einzelpreis": 149.00, "gesamtpreis": 298.00 }
  ],
  "netto": 698.00,
  "ust": 132.62,
  "brutto": 830.62
}
Prüfen: gültiges JSON ohne Codeblöcke, exakte Schlüssel, Datum ISO, Zahlen mit Punkt.`,
      },
      {
        title: 'Abgleich Rechnung ↔ Bestellung (ERP)',
        content: `Vergleiche die folgende Eingangsrechnung mit der zugehörigen Bestellung aus dem ERP-System. Liste alle Abweichungen einzeln auf und gib pro Abweichung an: Position, Art der Abweichung, Wert laut Bestellung, Wert laut Rechnung. Positionen ohne Abweichung kennzeichne mit "OK".

Bestellung aus dem ERP (B-2025-0311):
{
  "bestellnummer": "B-2025-0311",
  "lieferant": "NetParts GmbH",
  "positionen": [
    { "bezeichnung": "Netzwerkkabel Cat6 5m", "menge": 20, "einzelpreis": 3.90 },
    { "bezeichnung": "Switch 8-Port", "menge": 2, "einzelpreis": 45.00 },
    { "bezeichnung": "Patchpanel 24-Port", "menge": 1, "einzelpreis": 89.00 }
  ]
}

Eingangsrechnung (RE-77812, Bestellbezug B-2025-0311):
{
  "rechnungsnummer": "RE-77812",
  "lieferant": "NetParts GmbH",
  "positionen": [
    { "bezeichnung": "Netzwerkkabel Cat6 5m", "menge": 25, "einzelpreis": 3.90 },
    { "bezeichnung": "Switch 8-Port", "menge": 2, "einzelpreis": 52.00 },
    { "bezeichnung": "Patchpanel 24-Port", "menge": 1, "einzelpreis": 89.00 },
    { "bezeichnung": "Kabelbinder-Set", "menge": 3, "einzelpreis": 7.50 }
  ]
}`,
        expectedOutput: `Genau 3 Abweichungen müssen gefunden werden:
1. Netzwerkkabel Cat6 5m — Mengenabweichung: bestellt 20, berechnet 25.
2. Switch 8-Port — Preisabweichung: bestellt 45,00 EUR, berechnet 52,00 EUR.
3. Kabelbinder-Set — nicht bestellt (Position ohne Bestellbezug).
Patchpanel 24-Port: OK.`,
      },
      {
        title: 'Rechnerische Prüfung',
        content: `Prüfe die folgende Rechnung rechnerisch: Stimmen die Positionssummen, der Nettobetrag, die ausgewiesene Umsatzsteuer (19 %) und der Bruttobetrag? Benenne jeden Fehler konkret und gib jeweils den korrekten Wert an.

Rechnung RE-2025-1093, TechDirect GmbH:
Pos. 1: 3 x Monitor 27 Zoll, Einzelpreis 219,00 EUR, Gesamt 657,00 EUR
Pos. 2: 5 x HDMI-Kabel 2m, Einzelpreis 9,80 EUR, Gesamt 49,00 EUR

Nettobetrag: 706,00 EUR
zzgl. 19 % USt: 118,14 EUR
Rechnungsbetrag: 824,14 EUR`,
        expectedOutput: `Positionssummen und Nettobetrag sind korrekt (657,00 + 49,00 = 706,00 EUR).
Fehler 1: Die USt ist falsch ausgewiesen — 19 % von 706,00 EUR sind 134,14 EUR, nicht 118,14 EUR.
Fehler 2: Der Bruttobetrag ist folglich falsch — korrekt wären 840,14 EUR statt 824,14 EUR.`,
      },
      {
        title: 'Kontierungsvorschlag',
        content: `Ordne jede Rechnungsposition genau einer der folgenden Kostenkategorien zu: Büromaterial, IT-Hardware, Software/Lizenzen, Fremdleistungen/Beratung, Reisekosten. Gib das Ergebnis als Markdown-Tabelle mit den Spalten "Position" und "Kostenkategorie" aus, ohne weitere Erläuterung.

Rechnungspositionen:
1. 10 x Ordner DIN A4, breit
2. 1 x Notebook Dell Latitude 5550
3. Jahreslizenz Projektmanagement-Software (12 Monate)
4. 8 Std. IT-Beratung Netzwerkkonfiguration
5. Bahnticket Köln–Berlin, Kundentermin am 14.05.2025`,
        expectedOutput: `| Position | Kostenkategorie |
| 1. Ordner DIN A4 | Büromaterial |
| 2. Notebook Dell Latitude 5550 | IT-Hardware |
| 3. Jahreslizenz Projektmanagement-Software | Software/Lizenzen |
| 4. IT-Beratung Netzwerkkonfiguration | Fremdleistungen/Beratung |
| 5. Bahnticket Köln–Berlin | Reisekosten |
Prüfen: Markdown-Tabelle, jede Position genau eine Kategorie, keine Zusatztexte.`,
      },
      {
        title: 'Skonto und Zahlungsziel',
        content: `Eine Rechnung hat folgende Daten:
- Rechnungsdatum: 05.06.2025
- Rechnungsbetrag (brutto): 2.380,00 EUR
- Zahlungsbedingungen: "Zahlbar innerhalb von 14 Tagen mit 2 % Skonto, innerhalb von 30 Tagen netto."

Berechne:
1. Bis zu welchem Datum muss gezahlt werden, um Skonto zu nutzen?
2. Wie hoch ist der Zahlbetrag bei Skontonutzung?
3. Bis zu welchem Datum ist die Rechnung spätestens ohne Skonto zu zahlen?

Gib die Daten im Format TT.MM.JJJJ und die Beträge in EUR an, mit kurzem Rechenweg.`,
        expectedOutput: `1. Skontofrist: 19.06.2025 (05.06.2025 + 14 Tage)
2. Zahlbetrag mit Skonto: 2.332,40 EUR (Skonto 2 % von 2.380,00 EUR = 47,60 EUR)
3. Zahlungsziel netto: 05.07.2025 (05.06.2025 + 30 Tage)`,
      },
      {
        title: 'Klärungs-E-Mail an Lieferant',
        content: `Beim Abgleich der Eingangsrechnung RE-77812 der NetParts GmbH mit unserer Bestellung B-2025-0311 wurden folgende Abweichungen festgestellt:
1. Netzwerkkabel Cat6 5m: bestellt 20 Stück, berechnet 25 Stück.
2. Switch 8-Port: vereinbarter Einzelpreis 45,00 EUR, berechnet 52,00 EUR.
3. Kabelbinder-Set (3 x 7,50 EUR): wurde nicht bestellt.

Formuliere eine professionelle, höfliche E-Mail auf Deutsch an den Lieferanten (Ansprechpartner: Herr Krause), in der du die Abweichungen konkret benennst, um Prüfung und eine korrigierte Rechnung bittest und darauf hinweist, dass die Zahlung bis zur Klärung zurückgestellt wird. Mit Betreffzeile.`,
        expectedOutput: null,
      },
      {
        title: 'Freigabeentscheidung (JSON)',
        content: `Entscheide anhand der folgenden Freigaberegeln über die Rechnung und gib ausschließlich ein JSON-Objekt mit genau diesen Schlüsseln aus: entscheidung (einer der Werte "freigeben", "zurueckweisen", "pruefen") und begruendung (ein Satz auf Deutsch). Keine Codeblöcke, kein weiterer Text.

Freigaberegeln (in dieser Reihenfolge anwenden):
1. Bruttobetrag über 5.000,00 EUR -> "zurueckweisen"
2. Kein Bestellbezug vorhanden ODER Preisabweichung zur Bestellung über 2 % -> "pruefen"
3. Andernfalls -> "freigeben"

Rechnungsdaten:
- Rechnungsnummer: RE-2025-0655
- Bruttobetrag: 1.845,20 EUR
- Bestellbezug: B-2025-0198 (vorhanden)
- Preisabweichung zur Bestellung: 1,4 %`,
        expectedOutput: `{ "entscheidung": "freigeben", "begruendung": "..." }
Prüfen: entscheidung muss "freigeben" sein (Betrag unter 5.000 EUR, Bestellbezug vorhanden, Abweichung 1,4 % unter 2 %); Begründung nennt die erfüllten Regeln; nur JSON, keine Codeblöcke.`,
      },
      {
        title: 'Duplikatprüfung',
        content: `In der Kreditorenbuchhaltung sind die folgenden zwei Rechnungen erfasst. Handelt es sich wahrscheinlich um ein Duplikat (dieselbe Forderung doppelt eingereicht)? Gib eine klare Einschätzung (Ja/Nein), begründe sie anhand konkreter Indizien und gib eine Handlungsempfehlung.

Rechnung A:
- Rechnungsnummer: RE-2025-0788
- Datum: 02.04.2025
- Lieferant: TechSupply GmbH
- Betrag: 1.428,00 EUR brutto
- Bestellbezug: B-2025-0245

Rechnung B:
- Rechnungsnummer: RE-2025-0801
- Datum: 04.04.2025
- Lieferant: TechSupply GmbH
- Betrag: 1.428,00 EUR brutto
- Bestellbezug: B-2025-0245`,
        expectedOutput: `Ja, sehr wahrscheinlich ein Duplikat.
Indizien: gleicher Lieferant, identischer Betrag, identischer Bestellbezug, nur 2 Tage Abstand; abweichende Rechnungsnummer ist typisch für eine erneut ausgestellte/gesendete Rechnung.
Empfehlung: Zahlung anhalten, beim Lieferanten klären, nur eine der beiden Rechnungen buchen/zahlen.`,
      },
    ],
  },
  {
    name: 'Invoice Agent (Pipeline)',
    description:
      'Self-contained evals matching the real invoice-agent pipeline prompts (system prompts from FoundryAgentFactory.cs, user format from GptExecutors.cs): PO judge, reconcile approve/ask, reply interpreter.',
    prompts: [
      {
        title: 'Judge: ambiguous PO candidates',
        systemText: `You match a supplier invoice to one purchase order from a candidate list. Vendor names
on invoices rarely match the ERP partner exactly (legal-entity suffixes, abbreviations),
and totals can differ by tax or rounding. Weigh vendor/partner similarity, total, and date.
Reply with ONLY the exact PurchaseOrder name (e.g. "P00012") of the single best match, or
the single word NONE if you cannot confidently identify one. No other text.`,
        content: `Invoice: vendor="Nordlicht Handels GmbH & Co. KG", total=4165.00 EUR, date=2026-06-12, PO-printed-on-invoice=none.
Purchase-order candidates:
- P00018: partner="Nordlicht Handel", total=4165.00 EUR, date=2026-06-05, state=purchase
- P00019: partner="Nordlicht Handel", total=4165.00 EUR, date=2026-04-02, state=purchase
- P00007: partner="Süddeutsche Bürotechnik AG", total=4165.00 EUR, date=2026-06-10, state=purchase`,
        expectedOutput: `P00018

Pass: output contains "P00018" and nothing contradicting it (ideally the bare PO name).
Fail: P00019 (stale date), P00007 (wrong partner despite matching total), NONE, or any
explanation text around the answer (the parser does a substring match, so extra prose is
tolerated at runtime — but a well-behaved model returns only the name).`,
      },
      {
        title: 'Reconcile: folded discount → APPROVE',
        systemText: RECONCILE_SYSTEM,
        content: `Invoice: vendor="Süddeutsche Bürotechnik AG", total=2617.30 EUR, date=2026-06-20.
Invoice positions:
- Dokumentenscanner DS-940: qty=5, amount=2450.00
- Wartungspauschale Juni: qty=1, amount=167.30
Matched purchase order P00021: total=2617.30 EUR.
PO positions:
- Dokumentenscanner DS-940: qty=5, unit_price=515.00, total=2575.00
- Wartungspauschale: qty=1, unit_price=167.30, total=167.30
- Aktionsrabatt: qty=1, unit_price=-125.00, total=-125.00
Why it needs review: totals match but line counts differ (2 vs 3)`,
        expectedOutput: `APPROVE | <one short reason>
e.g. APPROVE | Totals match; scanner line difference is the PO's itemized Aktionsrabatt folded into the invoice price.

Pass: first token is APPROVE, followed by | and a reason.
Fail: ASK (asking about the Rabatt line or the 2450 vs 2575 scanner price — that gap is
exactly the -125.00 discount), or any output not starting with APPROVE.`,
      },
      {
        title: 'Reconcile: quantity mismatch → ASK',
        systemText: RECONCILE_SYSTEM,
        content: `Invoice: vendor="Nordlicht Handels GmbH & Co. KG", total=5220.00 EUR, date=2026-06-25.
Invoice positions:
- Höhenverstellbarer Schreibtisch E5: qty=12, amount=5220.00
Matched purchase order P00018: total=4350.00 EUR.
PO positions:
- Höhenverstellbarer Schreibtisch E5: qty=10, unit_price=435.00, total=4350.00
Why it needs review: totals differ (5220.00 vs 4350.00)`,
        expectedOutput: `First line exactly "ASK", then a short German note using "du", no salutation/sign-off, e.g.:

ASK
Die Rechnung von Nordlicht listet 12 Schreibtische E5 (5.220,00 EUR), bestellt waren laut P00018 aber nur 10 (4.350,00 EUR). Wurden tatsächlich 12 geliefert, oder stimmt die Rechnung nicht?

Pass: first line is ASK; body is German, informal "du", mentions the 12-vs-10 quantity
(or the 870 EUR gap) exactly once, ends without signature.
Fail: APPROVE (20% gap is far outside tolerance), English body, "Sehr geehrte…", or
restating the same discrepancy as both a quantity and a total difference.`,
      },
      {
        title: 'Reply interpreter: nachgebucht → RECHECK',
        systemText: `You read an orderer's free-text reply (usually German) to a clarification email about an
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
re-check, even if they also sound confident it is fine now.`,
        content: `Schriftverkehr mit dem Besteller zu dieser Rechnung (chronologisch):
---
Agent fragt:
Die Rechnung von Nordlicht Handels GmbH & Co. KG über 4.165,00 EUR vom 12.06.2026 passt zu keiner offenen Bestellung. Kannst du sagen, zu welcher Bestellung sie gehört?

Besteller antwortet:
Sorry, das war mein Fehler — die Bestellung hatte ich nur als Entwurf gespeichert. Habe sie eben in Odoo nachgebucht (P00023), sollte jetzt alles passen. Schau bitte nochmal.
---
Interpretiere die LETZTE Antwort des Bestellers im Kontext des gesamten Verlaufs.`,
        expectedOutput: `RECHECK | <one short note>
e.g. RECHECK | Besteller hat P00023 in Odoo nachgebucht

Pass: single line starting with RECHECK and containing a |.
Fail: APPROVED | P00023 | ... — the classic miss: the model latches onto the named PO and
the confident tone instead of the "nachgebucht, schau nochmal" trigger.`,
      },
    ],
  },
];

// ---------------------------------------------------------------------------
// Insert
// ---------------------------------------------------------------------------

function main() {
  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  // Ledger of what this script has ever seeded. `scope` is the group name for a
  // prompt and empty for a toolset.
  db.exec(
    `CREATE TABLE IF NOT EXISTS __app_seeds (
       kind TEXT NOT NULL,
       scope TEXT NOT NULL,
       name TEXT NOT NULL,
       seeded_at INTEGER NOT NULL,
       PRIMARY KEY (kind, scope, name)
     )`,
  );
  const wasSeeded = db.prepare(
    'SELECT 1 FROM __app_seeds WHERE kind = ? AND scope = ? AND name = ?',
  );
  const markSeeded = db.prepare(
    'INSERT OR IGNORE INTO __app_seeds (kind, scope, name, seeded_at) VALUES (?, ?, ?, ?)',
  );

  const toolsetByName = db.prepare('SELECT id FROM toolsets WHERE name = ?');
  const insertToolset = db.prepare(
    `INSERT INTO toolsets (name, description, kind, mcp_url, mcp_headers, created_at, updated_at)
     VALUES (?, ?, 'manual', NULL, NULL, ?, ?)`,
  );
  const insertTool = db.prepare(
    `INSERT INTO tools
       (toolset_id, name, description, parameters_json, mock_response, enabled, source, first_seen_at, last_seen_at)
     VALUES (?, ?, ?, ?, ?, 1, 'manual', ?, ?)`,
  );

  const groupByName = db.prepare('SELECT id FROM prompt_groups WHERE name = ?');
  const insertGroup = db.prepare(
    'INSERT INTO prompt_groups (name, description, sort_order, created_at) VALUES (?, ?, ?, ?)',
  );
  const promptExists = db.prepare(
    'SELECT id FROM prompts WHERE group_id = ? AND title = ?',
  );
  const insertPrompt = db.prepare(
    `INSERT INTO prompts
       (group_id, title, content, expected_output, system_prompt_id, system_prompt_mode, custom_system_text,
        tool_mode, tool_choice, max_turns, sort_order, created_at, updated_at)
     VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)`,
  );
  const linkToolset = db.prepare(
    'INSERT INTO prompt_toolsets (prompt_id, toolset_id, sort_order) VALUES (?, ?, ?)',
  );

  const seedAll = db.transaction(() => {
    // Toolsets first: prompts reference them by name.
    const toolsetIdByName = new Map();
    for (const toolset of TOOLSETS) {
      const existing = toolsetByName.get(toolset.name);
      if (existing) {
        // Present already: adopt it, and backfill the ledger for a database
        // seeded before the ledger existed.
        toolsetIdByName.set(toolset.name, existing.id);
        markSeeded.run('toolset', '', toolset.name, Date.now());
        console.log(`[seed-prompts] toolset "${toolset.name}" already exists, skipping`);
        continue;
      }
      if (wasSeeded.get('toolset', '', toolset.name)) {
        console.log(`[seed-prompts] toolset "${toolset.name}" was seeded before and deleted, leaving it out`);
        continue;
      }

      const now = Date.now();
      const { lastInsertRowid: toolsetId } = insertToolset.run(
        toolset.name,
        toolset.description,
        now,
        now,
      );
      for (const tool of toolset.tools) {
        insertTool.run(
          toolsetId,
          tool.name,
          tool.description,
          JSON.stringify(tool.parameters),
          JSON.stringify(tool.mockResponse, null, 2),
          now,
          now,
        );
      }
      toolsetIdByName.set(toolset.name, toolsetId);
      markSeeded.run('toolset', '', toolset.name, now);
      console.log(
        `[seed-prompts] created toolset "${toolset.name}" (${toolset.tools.length} tools)`,
      );
    }

    GROUPS.forEach((group, groupIndex) => {
      const now = Date.now();
      let groupId = groupByName.get(group.name)?.id;
      if (groupId === undefined) {
        groupId = insertGroup.run(group.name, group.description, groupIndex, now).lastInsertRowid;
        console.log(`[seed-prompts] created group "${group.name}"`);
      }

      let added = 0;
      let skippedAsDeleted = 0;
      group.prompts.forEach((prompt, promptIndex) => {
        if (promptExists.get(groupId, prompt.title)) {
          // Backfill: a database seeded before the ledger existed.
          markSeeded.run('prompt', group.name, prompt.title, now);
          return;
        }
        if (wasSeeded.get('prompt', group.name, prompt.title)) {
          // Seeded once and deleted since — that was a deliberate choice, so
          // do not resurrect it.
          skippedAsDeleted += 1;
          return;
        }

        const toolMode = prompt.toolMode ?? 'none';
        const { lastInsertRowid: promptId } = insertPrompt.run(
          groupId,
          prompt.title,
          prompt.content,
          prompt.expectedOutput,
          prompt.systemText ? 'override' : 'append',
          prompt.systemText ?? null,
          toolMode,
          prompt.toolChoice ?? null,
          prompt.maxTurns ?? 6,
          (promptIndex + 1) * 10,
          now,
          now,
        );

        (prompt.toolsets ?? []).forEach((name, order) => {
          const toolsetId = toolsetIdByName.get(name);
          if (toolsetId === undefined) {
            // Only reachable if a seed prompt names a toolset this script does
            // not define — a bug in the seed data rather than a user action.
            throw new Error(
              `prompt "${prompt.title}" references unknown toolset "${name}"`,
            );
          }
          linkToolset.run(promptId, toolsetId, order);
        });

        markSeeded.run('prompt', group.name, prompt.title, now);
        added += 1;
      });

      const deletedNote =
        skippedAsDeleted > 0 ? ` (${skippedAsDeleted} previously deleted, left out)` : '';
      console.log(
        added === 0
          ? `[seed-prompts] group "${group.name}" up to date${deletedNote}`
          : `[seed-prompts] group "${group.name}": added ${added} prompt(s)${deletedNote}`,
      );
    });
  });

  seedAll();
  db.close();
  console.log(`[seed-prompts] done (${dbPath})`);
}

main();
