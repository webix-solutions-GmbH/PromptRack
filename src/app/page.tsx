export default function Home() {
  return (
    <div className="flex flex-1 flex-col items-start gap-2 p-8">
      <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
        Dashboard
      </h1>
      <p className="max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
        The dashboard will land here — a summary of machines, recent runs,
        and model comparisons.
      </p>
    </div>
  );
}
