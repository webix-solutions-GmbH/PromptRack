import { describe, expect, it } from 'vitest';
import { splitThinking } from './thinking';

describe('splitThinking', () => {
  it('passes text without a think block through unchanged', () => {
    expect(splitThinking('Hello world')).toEqual({
      thinking: null,
      answer: 'Hello world',
      thinkingClosed: true,
    });
  });

  it('splits a leading closed think block off the answer', () => {
    expect(splitThinking('<think>reasoning here</think>The answer.')).toEqual({
      thinking: 'reasoning here',
      answer: 'The answer.',
      thinkingClosed: true,
    });
  });

  it('tolerates leading whitespace before the tag and around the answer', () => {
    expect(splitThinking('\n  <think>\nstep 1\nstep 2\n</think>\n\nAnswer.')).toEqual({
      thinking: 'step 1\nstep 2',
      answer: 'Answer.',
      thinkingClosed: true,
    });
  });

  it('treats an unclosed block as still-streaming thinking with no answer yet', () => {
    expect(splitThinking('<think>still going')).toEqual({
      thinking: 'still going',
      answer: '',
      thinkingClosed: false,
    });
  });

  it('returns null thinking for an opened but still-empty block', () => {
    expect(splitThinking('<think>')).toEqual({
      thinking: null,
      answer: '',
      thinkingClosed: false,
    });
  });

  it('drops an empty closed block without inventing thinking', () => {
    expect(splitThinking('<think>  </think>Answer.')).toEqual({
      thinking: null,
      answer: 'Answer.',
      thinkingClosed: true,
    });
  });

  it('ignores a think tag that appears mid-answer', () => {
    const text = 'Use <think> tags to mark reasoning.';
    expect(splitThinking(text)).toEqual({
      thinking: null,
      answer: text,
      thinkingClosed: true,
    });
  });

  it('supports <thinking> and <reasoning> variants case-insensitively', () => {
    expect(splitThinking('<Thinking>a</Thinking>b').thinking).toBe('a');
    expect(splitThinking('<reasoning>a</reasoning>b').answer).toBe('b');
  });

  it('does not close a <thinking> block with a mismatched </think> tag', () => {
    expect(splitThinking('<thinking>uses </think> inside').thinkingClosed).toBe(false);
  });

  it('keeps later tag pairs in the answer untouched', () => {
    expect(splitThinking('<think>a</think>x <think>b</think> y').answer).toBe(
      'x <think>b</think> y',
    );
  });
});
