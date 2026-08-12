import Link from 'next/link';

export default function Forbidden() {
  return (
    <div className="flex flex-1 flex-col items-start gap-3 p-8">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Not allowed
      </h1>
      <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
        Your account does not have access to this page. Ask an administrator if you need it.
      </p>
      <Link
        href="/"
        className="text-sm text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
      >
        ← Back to the dashboard
      </Link>
    </div>
  );
}
