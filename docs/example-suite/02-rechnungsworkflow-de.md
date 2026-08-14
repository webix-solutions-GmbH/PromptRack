# Rechnungsworkflow (DE)

8 test cases.

How to create these: [README](README.md). Create the group with `create_test_group`, then one `create_test_case` per block below, in this order.

Group description (pass as `description` to `create_test_group`):

```text
Deutsche Prompts für einen Kreditoren-Workflow: Extraktion, ERP-Abgleich, Prüfung, Freigabe.
```

A German accounts-payable workflow: extraction, ERP reconciliation, arithmetic checking, coding, discount terms, supplier correspondence, release decision, duplicate detection.

All eight are plain one-shot tests: `tool_mode: none`, neither prompt slot filled, no toolsets — the `content` alone is the whole user message. **Keep the German text exactly as written** — the point of the group is German-language behaviour, and two test cases (`Kontierungsvorschlag`, `Freigabeentscheidung`) grade on German output strings.

---

## 1. Rechnungsdaten extrahieren

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Extrahiere die Daten aus der folgenden Rechnung. Gib ausschließlich gültiges JSON aus, ohne Markdown-Codeblöcke und ohne Erläuterung. Verwende genau diese Schlüssel: rechnungsnummer, datum (ISO 8601), lieferant, ust_id, positionen (Array aus Objekten mit bezeichnung, menge, einzelpreis, gesamtpreis), netto, ust, brutto. Beträge als Zahlen mit Punkt als Dezimaltrennzeichen.

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
Rechnungsbetrag: 830,62 EUR
```

`expected_output`:

```text
{
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
Prüfen: gültiges JSON ohne Codeblöcke, exakte Schlüssel, Datum ISO, Zahlen mit Punkt.
```

---

## 2. Abgleich Rechnung ↔ Bestellung (ERP)

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Vergleiche die folgende Eingangsrechnung mit der zugehörigen Bestellung aus dem ERP-System. Liste alle Abweichungen einzeln auf und gib pro Abweichung an: Position, Art der Abweichung, Wert laut Bestellung, Wert laut Rechnung. Positionen ohne Abweichung kennzeichne mit "OK".

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
}
```

`expected_output`:

```text
Genau 3 Abweichungen müssen gefunden werden:
1. Netzwerkkabel Cat6 5m — Mengenabweichung: bestellt 20, berechnet 25.
2. Switch 8-Port — Preisabweichung: bestellt 45,00 EUR, berechnet 52,00 EUR.
3. Kabelbinder-Set — nicht bestellt (Position ohne Bestellbezug).
Patchpanel 24-Port: OK.
```

---

## 3. Rechnerische Prüfung

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Prüfe die folgende Rechnung rechnerisch: Stimmen die Positionssummen, der Nettobetrag, die ausgewiesene Umsatzsteuer (19 %) und der Bruttobetrag? Benenne jeden Fehler konkret und gib jeweils den korrekten Wert an.

Rechnung RE-2025-1093, TechDirect GmbH:
Pos. 1: 3 x Monitor 27 Zoll, Einzelpreis 219,00 EUR, Gesamt 657,00 EUR
Pos. 2: 5 x HDMI-Kabel 2m, Einzelpreis 9,80 EUR, Gesamt 49,00 EUR

Nettobetrag: 706,00 EUR
zzgl. 19 % USt: 118,14 EUR
Rechnungsbetrag: 824,14 EUR
```

`expected_output`:

```text
Positionssummen und Nettobetrag sind korrekt (657,00 + 49,00 = 706,00 EUR).
Fehler 1: Die USt ist falsch ausgewiesen — 19 % von 706,00 EUR sind 134,14 EUR, nicht 118,14 EUR.
Fehler 2: Der Bruttobetrag ist folglich falsch — korrekt wären 840,14 EUR statt 824,14 EUR.
```

---

## 4. Kontierungsvorschlag

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Ordne jede Rechnungsposition genau einer der folgenden Kostenkategorien zu: Büromaterial, IT-Hardware, Software/Lizenzen, Fremdleistungen/Beratung, Reisekosten. Gib das Ergebnis als Markdown-Tabelle mit den Spalten "Position" und "Kostenkategorie" aus, ohne weitere Erläuterung.

Rechnungspositionen:
1. 10 x Ordner DIN A4, breit
2. 1 x Notebook Dell Latitude 5550
3. Jahreslizenz Projektmanagement-Software (12 Monate)
4. 8 Std. IT-Beratung Netzwerkkonfiguration
5. Bahnticket Köln–Berlin, Kundentermin am 14.05.2025
```

`expected_output`:

```text
| Position | Kostenkategorie |
| 1. Ordner DIN A4 | Büromaterial |
| 2. Notebook Dell Latitude 5550 | IT-Hardware |
| 3. Jahreslizenz Projektmanagement-Software | Software/Lizenzen |
| 4. IT-Beratung Netzwerkkonfiguration | Fremdleistungen/Beratung |
| 5. Bahnticket Köln–Berlin | Reisekosten |
Prüfen: Markdown-Tabelle, jede Position genau eine Kategorie, keine Zusatztexte.
```

---

## 5. Skonto und Zahlungsziel

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Eine Rechnung hat folgende Daten:
- Rechnungsdatum: 05.06.2025
- Rechnungsbetrag (brutto): 2.380,00 EUR
- Zahlungsbedingungen: "Zahlbar innerhalb von 14 Tagen mit 2 % Skonto, innerhalb von 30 Tagen netto."

Berechne:
1. Bis zu welchem Datum muss gezahlt werden, um Skonto zu nutzen?
2. Wie hoch ist der Zahlbetrag bei Skontonutzung?
3. Bis zu welchem Datum ist die Rechnung spätestens ohne Skonto zu zahlen?

Gib die Daten im Format TT.MM.JJJJ und die Beträge in EUR an, mit kurzem Rechenweg.
```

`expected_output`:

```text
1. Skontofrist: 19.06.2025 (05.06.2025 + 14 Tage)
2. Zahlbetrag mit Skonto: 2.332,40 EUR (Skonto 2 % von 2.380,00 EUR = 47,60 EUR)
3. Zahlungsziel netto: 05.07.2025 (05.06.2025 + 30 Tage)
```

---

## 6. Klärungs-E-Mail an Lieferant

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Beim Abgleich der Eingangsrechnung RE-77812 der NetParts GmbH mit unserer Bestellung B-2025-0311 wurden folgende Abweichungen festgestellt:
1. Netzwerkkabel Cat6 5m: bestellt 20 Stück, berechnet 25 Stück.
2. Switch 8-Port: vereinbarter Einzelpreis 45,00 EUR, berechnet 52,00 EUR.
3. Kabelbinder-Set (3 x 7,50 EUR): wurde nicht bestellt.

Formuliere eine professionelle, höfliche E-Mail auf Deutsch an den Lieferanten (Ansprechpartner: Herr Krause), in der du die Abweichungen konkret benennst, um Prüfung und eine korrigierte Rechnung bittest und darauf hinweist, dass die Zahlung bis zur Klärung zurückgestellt wird. Mit Betreffzeile.
```

`expected_output`:

_none — this test case is rated by reading the answer._

---

## 7. Freigabeentscheidung (JSON)

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
Entscheide anhand der folgenden Freigaberegeln über die Rechnung und gib ausschließlich ein JSON-Objekt mit genau diesen Schlüsseln aus: entscheidung (einer der Werte "freigeben", "zurueckweisen", "pruefen") und begruendung (ein Satz auf Deutsch). Keine Codeblöcke, kein weiterer Text.

Freigaberegeln (in dieser Reihenfolge anwenden):
1. Bruttobetrag über 5.000,00 EUR -> "zurueckweisen"
2. Kein Bestellbezug vorhanden ODER Preisabweichung zur Bestellung über 2 % -> "pruefen"
3. Andernfalls -> "freigeben"

Rechnungsdaten:
- Rechnungsnummer: RE-2025-0655
- Bruttobetrag: 1.845,20 EUR
- Bestellbezug: B-2025-0198 (vorhanden)
- Preisabweichung zur Bestellung: 1,4 %
```

`expected_output`:

```text
{ "entscheidung": "freigeben", "begruendung": "..." }
Prüfen: entscheidung muss "freigeben" sein (Betrag unter 5.000 EUR, Bestellbezug vorhanden, Abweichung 1,4 % unter 2 %); Begründung nennt die erfüllten Regeln; nur JSON, keine Codeblöcke.
```

---

## 8. Duplikatprüfung

- `tool_mode`: `none`
- prompts: none — leave `system_prompt` and `task_prompt` out

`content`:

```text
In der Kreditorenbuchhaltung sind die folgenden zwei Rechnungen erfasst. Handelt es sich wahrscheinlich um ein Duplikat (dieselbe Forderung doppelt eingereicht)? Gib eine klare Einschätzung (Ja/Nein), begründe sie anhand konkreter Indizien und gib eine Handlungsempfehlung.

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
- Bestellbezug: B-2025-0245
```

`expected_output`:

```text
Ja, sehr wahrscheinlich ein Duplikat.
Indizien: gleicher Lieferant, identischer Betrag, identischer Bestellbezug, nur 2 Tage Abstand; abweichende Rechnungsnummer ist typisch für eine erneut ausgestellte/gesendete Rechnung.
Empfehlung: Zahlung anhalten, beim Lieferanten klären, nur eine der beiden Rechnungen buchen/zahlen.
```
