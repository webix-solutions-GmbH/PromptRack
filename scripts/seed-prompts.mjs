#!/usr/bin/env node
/**
 * Seeds ready-to-run benchmark prompt groups.
 *
 * Like `init-db.mjs` this depends on nothing but `better-sqlite3`, so it can
 * run inside the standalone container image (`node scripts/seed-prompts.mjs`)
 * as well as in development (`npm run db:seed`).
 *
 * Idempotency: a group whose name already exists in `prompt_groups` is
 * skipped entirely — re-running never duplicates or overwrites anything.
 * Prompts deliberately embed all instructions and data inline (no
 * `system_prompts` dependency), so each one is self-contained.
 */
import path from 'node:path';
import Database from 'better-sqlite3';

const root = process.env.APP_ROOT ?? process.cwd();
const dataDir = process.env.DATA_DIR ?? path.join(root, 'data');
const dbPath = path.join(dataDir, 'app.db');

// ---------------------------------------------------------------------------
// Seed data
// ---------------------------------------------------------------------------

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
];

// ---------------------------------------------------------------------------
// Insert
// ---------------------------------------------------------------------------

function main() {
  const db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  const groupExists = db.prepare('SELECT id FROM prompt_groups WHERE name = ?');
  const insertGroup = db.prepare(
    'INSERT INTO prompt_groups (name, description, sort_order, created_at) VALUES (?, ?, ?, ?)',
  );
  const insertPrompt = db.prepare(
    `INSERT INTO prompts
       (group_id, title, content, expected_output, system_prompt_id, system_prompt_mode, custom_system_text, sort_order, created_at, updated_at)
     VALUES (?, ?, ?, ?, NULL, 'append', NULL, ?, ?, ?)`,
  );

  const seedAll = db.transaction(() => {
    GROUPS.forEach((group, groupIndex) => {
      if (groupExists.get(group.name)) {
        console.log(`[seed-prompts] group "${group.name}" already exists, skipping`);
        return;
      }
      const now = Date.now();
      const { lastInsertRowid: groupId } = insertGroup.run(
        group.name,
        group.description,
        groupIndex,
        now,
      );
      group.prompts.forEach((prompt, promptIndex) => {
        insertPrompt.run(
          groupId,
          prompt.title,
          prompt.content,
          prompt.expectedOutput,
          (promptIndex + 1) * 10,
          now,
          now,
        );
      });
      console.log(
        `[seed-prompts] created group "${group.name}" (${group.prompts.length} prompts)`,
      );
    });
  });

  seedAll();
  db.close();
  console.log(`[seed-prompts] done (${dbPath})`);
}

main();
