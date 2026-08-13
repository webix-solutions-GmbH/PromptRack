# General Capabilities

11 test cases.

How to create these: [README](README.md). Create the group with `create_test_group`, then one `create_test_case` per block below, in this order.

Group description (pass as `description` to `create_test_group`):

```text
Broad capability tests: reasoning, extraction, formatting, code. Language: English.
```

Reasoning, extraction, formatting, code, and three tool-calling tests. English. The three tool test cases need **Demo Utilities (mock)** to exist first.

Eight test cases are plain one-shot tests (`tool_mode: none`, no prompt) — for those, `create_test_case` needs only `group`, `title`, `content`, `expected_output`.

---

## 1. Three-bullet summary

- `tool_mode`: `none`
- prompt: none

`content`:

```text
Summarize the following text in exactly 3 bullet points. Each bullet point must be at most 15 words. Output only the bullet points, nothing else.

Text:
The shipping container, standardized in the 1960s, transformed global trade more than almost any other invention of the twentieth century. Before containerization, cargo was loaded piece by piece by large gangs of dockworkers, a slow and expensive process during which goods were frequently damaged or stolen. A single ship could spend more time in port than at sea. The standardized steel box changed the economics completely: cranes could move containers directly between ships, trucks, and trains without touching the contents, cutting loading costs by more than 90 percent and port time from weeks to hours. As a result, manufacturers could suddenly source components from anywhere in the world at low cost, which enabled the global supply chains we know today. Ports that invested early in container terminals, such as Rotterdam and Singapore, grew into major trade hubs, while many traditional harbor districts declined.
```

`expected_output`:

```text
Rating aid — the three bullets should cover roughly these points, each within 15 words:
- Containerization (1960s) replaced slow, costly piece-by-piece cargo loading.
- Standardized boxes cut loading costs over 90% and port time from weeks to hours.
- Cheap transport enabled global supply chains; early container ports became major hubs.
Check: exactly 3 bullets, no intro/outro text, each bullet has at most 15 words.
```

---

## 2. JSON extraction

- `tool_mode`: `none`
- prompt: none

`content`:

```text
Extract the order data from the confirmation below. Output only valid JSON, no markdown code fences, no explanation. Use exactly these keys: customer_name, order_number, order_date (ISO 8601), items (array of objects with name, quantity, unit_price), total.

Order confirmation:
Dear Emily Carter, thank you for your order #ORD-2024-1187 placed on March 5, 2024. We hereby confirm the following items: 2x Ergonomic Desk Chair at $249.00 each, 1x Standing Desk Frame at $399.00, and 3x Monitor Arm at $59.00 each. The total amount of your order is $1,074.00 and will be charged to your card on file.
```

`expected_output`:

```text
{
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
Check: valid JSON, no code fences, exact key names, numbers as numbers (not strings).
```

---

## 3. Multi-step logic

- `tool_mode`: `none`
- prompt: none

`content`:

```text
Anna, Ben, Clara, and David sit in a row of four seats numbered 1 to 4 from left to right.

- Anna does not sit at either end.
- Ben sits somewhere to the left of Anna.
- Clara sits immediately to the right of Anna.
- David sits at seat 4.

Who sits in which seat? Answer in one sentence.
```

`expected_output`:

```text
Ben sits in seat 1, Anna in seat 2, Clara in seat 3, David in seat 4.
(Unique solution: Anna must be in 2 or 3; Anna in 3 would put Clara in 4, conflicting with David, so Anna is in 2.)
```

---

## 4. Arithmetic word problem

- `tool_mode`: `none`
- prompt: none

`content`:

```text
A laptop has a list price of $1,200. During a sale it is discounted by 15%. A loyal customer receives an additional 10% off the sale price. Finally, 8% sales tax is applied to the discounted price. How much does the customer pay in total? Show your calculation steps and give the final amount rounded to the cent.
```

`expected_output`:

```text
$991.44
(1200 x 0.85 = 1020; 1020 x 0.90 = 918; 918 x 1.08 = 991.44)
```

---

## 5. Strict instruction following

- `tool_mode`: `none`
- prompt: none

`content`:

```text
What is the capital of France? Respond with exactly five words, all lowercase, no punctuation.
```

`expected_output`:

```text
Checklist: exactly 5 words / everything lowercase / no punctuation at all / contains "paris".
Example of a valid answer: "the capital city is paris"
```

---

## 6. Code generation

- `tool_mode`: `none`
- prompt: none

`content`:

```text
Write a Python function parse_duration(s) that converts a duration string such as "1h 30m 15s" into the total number of seconds as an int.

Requirements:
- The parts hours (h), minutes (m), and seconds (s) may each be present or absent, but always appear in that order, separated by single spaces.
- Examples: "1h 30m 15s" -> 5415, "90m" -> 5400, "2h" -> 7200, "45s" -> 45.
- Raise a ValueError for invalid input (empty string, unknown units, wrong order).
- Include a docstring with doctests covering the examples above.

Output only the code.
```

`expected_output`:

```text
Behavioral contract to check:
- parse_duration("1h 30m 15s") == 5415
- parse_duration("90m") == 5400
- parse_duration("2h") == 7200
- parse_duration("45s") == 45
- ValueError on "" and on invalid strings like "abc" or "30m 1h"
- Docstring with doctests present; output is code only.
```

---

## 7. Bug finding

- `tool_mode`: `none`
- prompt: none

`content`:

```text
The following Python function is supposed to return the moving average over every consecutive window of the given size, but it has a bug. Identify the bug, explain it in one or two sentences, and provide the corrected line.

def moving_average(values, window):
    """Return the average of each consecutive 'window' values."""
    result = []
    for i in range(len(values) - window):
        chunk = values[i:i + window]
        result.append(sum(chunk) / window)
    return result
```

`expected_output`:

```text
Off-by-one in the range: the last window is skipped. For example moving_average([1, 2, 3], 2) returns only [1.5] instead of [1.5, 2.5].
Corrected line: for i in range(len(values) - window + 1):
```

---

## 8. Long-form generation

- `tool_mode`: `none`
- prompt: none

`content`:

```text
Explain how DNS (the Domain Name System) works to a junior developer in about 300 words. Structure your answer with a short introduction, three sections with headings (the resolution steps, caching, and common record types), and a one-sentence summary at the end.
```

`expected_output`:

_none — this prompt is rated by reading the answer._

---

## 9. Tools: pick the right one

- `tool_mode`: `definitions`
- `toolsets`: `Demo Utilities (mock)`
- prompt: none

`content`:

```text
What is the current temperature in Berlin, in degrees Celsius?
```

`expected_output`:

```text
Exactly one tool call: get_weather with city="Berlin".
Passing "unit": "celsius" is fine (and slightly better); omitting unit is also correct since celsius is the documented default.

Fail: calling search_products / convert_currency / get_stock_level, calling more than one tool, inventing a tool that was not offered, a wrong or empty city, or answering from memory with a made-up temperature instead of calling anything.
Note: this prompt runs in "definitions" mode — nothing is executed, so judge the call itself, not any answer.
```

---

## 10. Tools: chain two calls and do the math

- `tool_mode`: `execute`
- `toolsets`: `Demo Utilities (mock)`
- `max_turns`: 6
- prompt: none

`content`:

```text
Our catalogue is priced in euros. Using the available tools, find the cheapest laptop we sell and tell me what it costs in US dollars. State the product name and the final USD amount rounded to the cent.
```

`expected_output`:

```text
Dell Latitude 5450 — $964.76

Required behaviour:
- calls search_products (query about laptops), then convert_currency (EUR -> USD)
- identifies 890.00 EUR (Dell Latitude 5450) as the cheapest of the three results
- applies the returned rate itself: 890.00 x 1.084 = 964.76 exactly

Fail: picking the 1049.00 or 979.00 item, only calling one of the two tools, inventing an exchange rate instead of calling convert_currency, or arithmetic that is off by more than a cent.
```

---

## 11. Tools: restraint — no call needed

- `tool_mode`: `definitions`
- `toolsets`: `Demo Utilities (mock)`
- prompt: none

`content`:

```text
How many continents are there on Earth? Answer in one short sentence.
```

`expected_output`:

```text
No tool call at all, plus a direct answer naming seven continents.

This tests restraint: none of the offered tools (weather, product search, currency, stock) can answer a general-knowledge question, so a well-behaved model answers directly.
Fail: any tool call — the classic over-eager failure is calling search_products with "continents" because tools were offered at all. The result card shows "0 tool calls" and stop reason "the model answered" when it passes.
```
