import { desc } from 'drizzle-orm';
import { db } from '@/db';
import { systemPrompts } from '@/db/schema';
import { createSystemPrompt } from '@/actions/system-prompts';
import { SystemPromptRow } from '@/components/system-prompts/system-prompt-row';

export const dynamic = 'force-dynamic';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';

export default async function SystemPromptsPage() {
  const rows = await db.select().from(systemPrompts).orderBy(desc(systemPrompts.updatedAt));

  return (
    <div className="flex flex-1 flex-col gap-8 p-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          System Prompts
        </h1>
        <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
          Reusable base system prompts. Individual test prompts can append to or override these.
        </p>
      </div>

      <ul className="flex max-w-3xl flex-col gap-3">
        {rows.length === 0 && (
          <li className="rounded-lg border border-zinc-200 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
            No system prompts yet — add one below.
          </li>
        )}
        {rows.map((prompt) => (
          <SystemPromptRow key={prompt.id} prompt={prompt} />
        ))}
      </ul>

      <section className="flex max-w-3xl flex-col gap-4 rounded-lg border border-zinc-200 p-6 dark:border-zinc-800">
        <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
          New system prompt
        </h2>
        <form action={createSystemPrompt} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="name">
              Name *
            </label>
            <input
              id="name"
              name="name"
              required
              placeholder="Coding assistant"
              className={inputClass}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="content">
              Content *
            </label>
            <textarea
              id="content"
              name="content"
              required
              rows={6}
              placeholder="You are an expert software engineer..."
              className={`${inputClass} font-mono`}
            />
          </div>
          <div>
            <button
              type="submit"
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
            >
              Create system prompt
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
