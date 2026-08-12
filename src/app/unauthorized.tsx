import Link from 'next/link';

export default function Unauthorized() {
  return (
    <div className="flex flex-1 flex-col items-start gap-3 p-8">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Sign in to continue
      </h1>
      <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
        Your session has ended.
      </p>
      <Link
        href="/login"
        className="text-sm text-zinc-500 underline-offset-2 hover:underline dark:text-zinc-400"
      >
        Go to sign-in
      </Link>
    </div>
  );
}
