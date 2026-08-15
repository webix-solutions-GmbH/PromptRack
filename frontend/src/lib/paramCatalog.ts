// Static catalog of suggested request-body params per endpoint platform. No
// backend involvement — this is UI sugar only: `ParamsEditor.vue` uses it to
// offer names/types/descriptions while still letting a caller type any key at
// all, since the wire format is "arbitrary JSON object, sent verbatim"
// (`backend/app/services/params.py`). min/max/step are UI hints, never
// enforced server-side — the provider is the authority on valid ranges.
import type { EndpointPlatform } from '../api/endpoints'

export type ParamType = 'number' | 'boolean' | 'string' | 'enum' | 'json'

export interface CatalogParam {
  name: string
  type: ParamType
  description: string
  values?: string[]
  min?: number
  max?: number
  step?: number
  placeholder?: string
}

export interface PlatformCatalog {
  label: string
  note?: string
  params: CatalogParam[]
}

/** Keys `backend/app/services/llm.py` owns — refused by name server-side.
 * Mirrored here so the editor can warn inline before the request round-trips. */
export const RESERVED_PARAM_KEYS: readonly string[] = [
  'model',
  'messages',
  'stream',
  'stream_options',
  'tools',
  'tool_choice',
]

const COMMON_PARAMS: CatalogParam[] = [
  { name: 'temperature', type: 'number', description: 'Sampling randomness — 0 is deterministic, higher is more varied.', min: 0, max: 2, step: 0.1 },
  { name: 'top_p', type: 'number', description: 'Nucleus sampling — consider only the top tokens covering this cumulative probability.', min: 0, max: 1, step: 0.05 },
  { name: 'max_tokens', type: 'number', description: 'Cap on generated completion tokens.', min: 1 },
  { name: 'seed', type: 'number', description: 'Fixes the sampling seed for more reproducible output, where the provider supports it.' },
  { name: 'stop', type: 'json', description: 'String or array of strings — generation stops if one is produced.', placeholder: '["\\n\\n"]' },
  { name: 'frequency_penalty', type: 'number', description: 'Penalizes tokens by how often they have already appeared.', min: -2, max: 2 },
  { name: 'presence_penalty', type: 'number', description: 'Penalizes tokens that have appeared at all so far, encouraging new topics.', min: -2, max: 2 },
]

export const PARAM_CATALOG: Record<EndpointPlatform, PlatformCatalog> = {
  generic: {
    label: 'Generic (OpenAI-compatible)',
    note: 'Suggestions only — every key/value you add is sent verbatim in the request body.',
    params: [...COMMON_PARAMS],
  },
  openai: {
    label: 'OpenAI',
    note: 'Reasoning models (o-series, GPT-5) reject temperature/top_p and require max_completion_tokens instead of max_tokens.',
    params: [
      ...COMMON_PARAMS,
      { name: 'max_completion_tokens', type: 'number', description: "OpenAI's replacement for max_tokens; reasoning models require this spelling." },
      { name: 'reasoning_effort', type: 'enum', description: 'How much internal reasoning a reasoning model spends before answering.', values: ['minimal', 'low', 'medium', 'high'] },
      { name: 'verbosity', type: 'enum', description: 'Target length/detail of the final answer (GPT-5).', values: ['low', 'medium', 'high'] },
      { name: 'logprobs', type: 'boolean', description: 'Return log probabilities for each output token.' },
      { name: 'top_logprobs', type: 'number', description: 'Number of most-likely tokens to return log probabilities for, per position.', min: 0, max: 20 },
      { name: 'logit_bias', type: 'json', description: 'Map of token id to bias, applied to that token\'s logits before sampling.' },
      { name: 'service_tier', type: 'enum', description: 'Requested processing tier for the request.', values: ['auto', 'default', 'flex', 'priority'] },
    ],
  },
  ollama: {
    label: 'Ollama',
    note: 'Over the OpenAI-compatible protocol Ollama accepts only these (max_tokens maps to num_predict). Context window (num_ctx), think and keep_alive are native-API-only — set them via the Modelfile or ollama CLI; adding them here sends keys Ollama silently ignores.',
    params: [...COMMON_PARAMS],
  },
  vllm: {
    label: 'vLLM',
    note: 'vLLM accepts these extras directly in the request body and rejects unknown fields.',
    params: [
      ...COMMON_PARAMS,
      { name: 'top_k', type: 'number', description: 'Restrict sampling to the top K candidate tokens; -1 disables.' },
      { name: 'min_p', type: 'number', description: 'Minimum token probability, relative to the most likely token.', min: 0, max: 1 },
      { name: 'repetition_penalty', type: 'number', description: 'Penalizes repeated tokens; 1.0 = off.' },
      { name: 'min_tokens', type: 'number', description: 'Minimum number of tokens to generate before allowing an end-of-sequence token.' },
      { name: 'ignore_eos', type: 'boolean', description: 'Keep generating past the end-of-sequence token, up to max_tokens.' },
      { name: 'stop_token_ids', type: 'json', description: 'Array of token ids that stop generation, in addition to stop strings.', placeholder: '[128001]' },
      { name: 'chat_template_kwargs', type: 'json', description: "Variables passed to the model's chat template — e.g. toggling Qwen3 thinking.", placeholder: '{"enable_thinking": false}' },
      { name: 'guided_json', type: 'json', description: 'Constrain output to a JSON schema.' },
      { name: 'guided_choice', type: 'json', description: 'Constrain output to one of a fixed set of strings.', placeholder: '["yes", "no"]' },
      { name: 'logprobs', type: 'boolean', description: 'Return log probabilities for each output token.' },
      { name: 'top_logprobs', type: 'number', description: 'Number of most-likely tokens to return log probabilities for, per position.' },
    ],
  },
  lmstudio: {
    label: 'LM Studio',
    note: 'LM Studio ignores unknown keys.',
    params: [
      ...COMMON_PARAMS,
      { name: 'top_k', type: 'number', description: 'Restrict sampling to the top K candidate tokens.' },
      { name: 'repeat_penalty', type: 'number', description: 'Penalizes repeated tokens.' },
      { name: 'logit_bias', type: 'json', description: 'Map of token id to bias, applied to that token\'s logits before sampling.' },
      { name: 'ttl', type: 'number', description: 'Seconds to keep the model loaded after the request.' },
    ],
  },
}
