import { describe, expect, it } from 'vitest';
import { computeTokensPerSec } from './llm';
import { aggregate, type TurnMetrics } from './tool-loop';

function turn(overrides: Partial<TurnMetrics> = {}): TurnMetrics {
  return {
    index: 0,
    ttftMs: 100,
    durationMs: 1000,
    promptTokens: 20,
    completionTokens: 45,
    tokensEstimated: false,
    finishReason: 'stop',
    toolCallCount: 0,
    ...overrides,
  };
}

describe('aggregate', () => {
  it('reduces to the single-turn numbers a plain prompt always produced', () => {
    const single = turn({ ttftMs: 500, durationMs: 2000, completionTokens: 100 });

    const result = aggregate([single]);

    expect(result.ttftMs).toBe(500);
    expect(result.durationMs).toBe(2000);
    expect(result.completionTokens).toBe(100);
    expect(result.tokensPerSec).toBeCloseTo(computeTokensPerSec(100, 2000, 500)!, 6);
  });

  it('takes ttft from the first turn only', () => {
    const result = aggregate([
      turn({ index: 0, ttftMs: 120 }),
      turn({ index: 1, ttftMs: 900 }),
    ]);

    expect(result.ttftMs).toBe(120);
  });

  it('sums durations and completion tokens across turns', () => {
    const result = aggregate([
      turn({ index: 0, durationMs: 1000, completionTokens: 10 }),
      turn({ index: 1, durationMs: 1500, completionTokens: 25 }),
    ]);

    expect(result.durationMs).toBe(2500);
    expect(result.completionTokens).toBe(35);
  });

  it('divides by the sum of the per-turn generation windows, not total duration', () => {
    // Turn 1: 1000 - 200 = 800ms. Turn 2: 1200 - 400 = 800ms. Total 1600ms.
    const result = aggregate([
      turn({ index: 0, ttftMs: 200, durationMs: 1000, completionTokens: 40 }),
      turn({ index: 1, ttftMs: 400, durationMs: 1200, completionTokens: 40 }),
    ]);

    expect(result.tokensPerSec).toBeCloseTo(80 / 1.6, 6);
    // Naively using (totalDuration - firstTtft) would overstate the window and
    // understate the rate, because later prefills would be counted as generation.
    expect(result.tokensPerSec).not.toBeCloseTo(computeTokensPerSec(80, 2200, 200)!, 6);
  });

  it('sums prompt tokens, keeping null only when no turn reported any', () => {
    expect(aggregate([turn({ promptTokens: 20 }), turn({ promptTokens: 35 })]).promptTokens).toBe(
      55,
    );
    expect(aggregate([turn({ promptTokens: null }), turn({ promptTokens: 35 })]).promptTokens).toBe(
      35,
    );
    expect(
      aggregate([turn({ promptTokens: null }), turn({ promptTokens: null })]).promptTokens,
    ).toBeNull();
  });

  it('flags the whole run as estimated when any single turn was', () => {
    expect(aggregate([turn(), turn({ tokensEstimated: true })]).tokensEstimated).toBe(true);
    expect(aggregate([turn(), turn()]).tokensEstimated).toBe(false);
  });

  it('treats a turn with no ttft as pure generation', () => {
    const result = aggregate([turn({ ttftMs: null, durationMs: 500, completionTokens: 50 })]);

    expect(result.tokensPerSec).toBeCloseTo(100, 6);
  });

  it('never produces a negative generation window', () => {
    // A clock skew or a usage-only trailing chunk can put ttft past duration.
    const result = aggregate([
      turn({ ttftMs: 900, durationMs: 400, completionTokens: 10 }),
      turn({ ttftMs: 100, durationMs: 1100, completionTokens: 10 }),
    ]);

    expect(result.tokensPerSec).toBeCloseTo(20, 6);
  });

  it('returns empty metrics when no turn ran at all', () => {
    expect(aggregate([])).toEqual({
      ttftMs: null,
      durationMs: 0,
      promptTokens: null,
      completionTokens: 0,
      tokensEstimated: true,
      tokensPerSec: null,
    });
  });
});
