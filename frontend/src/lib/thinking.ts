// Reasoning models (Qwen, DeepSeek R1, ...) prefix their answer with a
// `<think>...</think>` block streamed as plain content. For display we split
// it off so the UI can tuck it behind a collapsed toggle. Byte-for-byte port
// of `git show master:src/lib/thinking.ts` — kept pure so the run detail
// view can call it on every streamed delta with no round trip.

export interface SplitThinkingResult {
  /** Content of the leading think block, null when there is none. */
  thinking: string | null
  /** The response with the think block removed. */
  answer: string
  /** False while the block is still open (streaming, or truncated output). */
  thinkingClosed: boolean
}

const OPEN_TAG = /^\s*<(think|thinking|reasoning)>\s*/i

/**
 * Splits a leading `<think>`/`<thinking>`/`<reasoning>` block off a response.
 * Only a block at the very start counts — a tag mentioned mid-answer is answer
 * text, not reasoning. An unclosed block swallows the rest of the text.
 */
export function splitThinking(text: string): SplitThinkingResult {
  const open = text.match(OPEN_TAG)
  if (!open) {
    return { thinking: null, answer: text, thinkingClosed: true }
  }

  const tag = open[1]!.toLowerCase()
  const rest = text.slice(open[0].length)
  const closeIndex = rest.toLowerCase().indexOf(`</${tag}>`)

  if (closeIndex === -1) {
    const thinking = rest.trimEnd()
    return {
      thinking: thinking.length > 0 ? thinking : null,
      answer: '',
      thinkingClosed: false,
    }
  }

  const thinking = rest.slice(0, closeIndex).trim()
  const answer = rest.slice(closeIndex + `</${tag}>`.length).replace(/^\s+/, '')
  return {
    thinking: thinking.length > 0 ? thinking : null,
    answer,
    thinkingClosed: true,
  }
}
