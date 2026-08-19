// A reasoning model's chain of thought reaches this app one of two ways, and the
// same model on two endpoints picks different ones: inline in the answer wrapped
// in `<think>` tags (Ollama, vLLM without `--reasoning-parser`), or on its own
// channel, which the backend stores in `run_results.reasoning_text`.
//
// `resolveThinking` takes both and returns one answer/thinking pair, so no view
// has to know which shape its endpoint produced. Pure, so the run detail view can
// call it on every streamed delta with no round trip.

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

/**
 * One answer/thinking pair out of whichever shape the endpoint produced.
 *
 * Separately-captured reasoning wins and the text is left alone: the provider
 * already drew the boundary, so re-parsing would only find a `<think>` the model
 * wrote *about* thinking. `thinkingClosed` is true there because reasoning is
 * only ever stored on a finished row; a run still streaming arrives with
 * `reasoning` null and keeps the inline path's honest "still open" mark.
 */
export function resolveThinking(
  reasoning: string | null | undefined,
  text: string | null | undefined,
): SplitThinkingResult {
  const answer = text ?? ''
  if (reasoning) {
    return { thinking: reasoning, answer, thinkingClosed: true }
  }
  return splitThinking(answer)
}
