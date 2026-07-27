'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

/**
 * Renders LLM output as GitHub-flavored markdown. Raw HTML in the response is
 * not rendered (react-markdown escapes it by default), so model output can't
 * inject markup. Kept intentionally unstyled at the edges — the caller wraps
 * it in whatever box the surrounding layout uses.
 */
export function MarkdownResponse({ text }: { text: string }) {
  return (
    <div className="prose prose-sm max-w-none prose-zinc break-words dark:prose-invert prose-pre:whitespace-pre-wrap prose-pre:break-words prose-ul:my-1 prose-ol:my-1 prose-li:my-0 prose-li:leading-snug [&_li>p]:my-0">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
