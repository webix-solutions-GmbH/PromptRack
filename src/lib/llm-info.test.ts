import { describe, expect, it } from 'vitest';
import {
  apiRoot,
  extractLmStudioModelDetails,
  extractModelEntryDetails,
  extractOllamaShowDetails,
  parseLlmInfo,
} from './llm-info';

describe('apiRoot', () => {
  it('strips a trailing /v1', () => {
    expect(apiRoot('http://host:8000/v1')).toBe('http://host:8000');
  });

  it('strips trailing slashes before /v1', () => {
    expect(apiRoot('http://host:8000/v1/')).toBe('http://host:8000');
  });

  it('leaves a base without /v1 alone', () => {
    expect(apiRoot('http://host:3000/agent-val/api/mock-llm')).toBe(
      'http://host:3000/agent-val/api/mock-llm',
    );
  });

  it('does not strip v1 embedded mid-path', () => {
    expect(apiRoot('http://host/v1/proxy')).toBe('http://host/v1/proxy');
  });
});

describe('extractModelEntryDetails', () => {
  const vllmPayload = {
    object: 'list',
    data: [
      {
        id: 'qwen3-32b',
        object: 'model',
        created: 1745000000,
        owned_by: 'vllm',
        root: '/models/qwen3-32b',
        parent: null,
        max_model_len: 32768,
        permission: [{ id: 'perm-1' }],
      },
      { id: 'other-model', object: 'model', max_model_len: 4096 },
    ],
  };

  it('keeps the scalar fields of the matching entry only', () => {
    expect(extractModelEntryDetails(vllmPayload, 'qwen3-32b')).toEqual({
      owned_by: 'vllm',
      root: '/models/qwen3-32b',
      max_model_len: '32768',
    });
  });

  it('returns empty for an unknown model id', () => {
    expect(extractModelEntryDetails(vllmPayload, 'missing')).toEqual({});
  });

  it('returns empty for malformed payloads', () => {
    expect(extractModelEntryDetails(null, 'x')).toEqual({});
    expect(extractModelEntryDetails({ data: 'nope' }, 'x')).toEqual({});
    expect(extractModelEntryDetails('nonsense', 'x')).toEqual({});
  });
});

describe('extractOllamaShowDetails', () => {
  const showPayload = {
    details: {
      format: 'gguf',
      family: 'qwen3',
      parameter_size: '32.8B',
      quantization_level: 'Q4_K_M',
      families: ['qwen3'],
    },
    model_info: {
      'general.architecture': 'qwen3',
      'general.parameter_count': 32800000000,
      'qwen3.context_length': 40960,
      'qwen3.embedding_length': 5120,
      'qwen3.attention.head_count': 64,
    },
    capabilities: ['completion', 'tools', 'thinking'],
  };

  it('flattens details, well-known model_info keys and capabilities', () => {
    expect(extractOllamaShowDetails(showPayload)).toEqual({
      format: 'gguf',
      family: 'qwen3',
      parameter_size: '32.8B',
      quantization_level: 'Q4_K_M',
      architecture: 'qwen3',
      parameter_count: '32800000000',
      context_length: '40960',
      embedding_length: '5120',
      capabilities: 'completion, tools, thinking',
    });
  });

  it('returns empty for malformed payloads', () => {
    expect(extractOllamaShowDetails(null)).toEqual({});
    expect(extractOllamaShowDetails({ details: 'nope' })).toEqual({});
  });
});

describe('extractLmStudioModelDetails', () => {
  it('keeps the scalar fields', () => {
    expect(
      extractLmStudioModelDetails({
        id: 'qwen3-32b',
        object: 'model',
        type: 'llm',
        publisher: 'qwen',
        arch: 'qwen3',
        compatibility_type: 'gguf',
        quantization: '4bit',
        state: 'loaded',
        max_context_length: 40960,
        loaded_context_length: 4096,
      }),
    ).toEqual({
      type: 'llm',
      publisher: 'qwen',
      arch: 'qwen3',
      compatibility_type: 'gguf',
      quantization: '4bit',
      state: 'loaded',
      max_context_length: '40960',
    });
  });

  it('returns empty for malformed payloads', () => {
    expect(extractLmStudioModelDetails(null)).toEqual({});
    expect(extractLmStudioModelDetails([1, 2])).toEqual({});
  });
});

describe('parseLlmInfo', () => {
  it('round-trips a stored snapshot', () => {
    const raw = JSON.stringify({
      server: 'vLLM',
      version: '0.8.5',
      details: { max_model_len: '32768' },
    });
    expect(parseLlmInfo(raw)).toEqual({
      server: 'vLLM',
      version: '0.8.5',
      details: { max_model_len: '32768' },
    });
  });

  it('returns null for null, invalid JSON and non-object JSON', () => {
    expect(parseLlmInfo(null)).toBeNull();
    expect(parseLlmInfo('{broken')).toBeNull();
    expect(parseLlmInfo('"just a string"')).toBeNull();
  });

  it('drops non-string detail values instead of failing', () => {
    const raw = JSON.stringify({ server: null, version: null, details: { a: '1', b: 2 } });
    expect(parseLlmInfo(raw)).toEqual({ server: null, version: null, details: { a: '1' } });
  });
});
