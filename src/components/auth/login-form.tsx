'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { authClient } from '@/lib/auth-client';
import { BASE_PATH } from '@/lib/base-path';

const inputClass =
  'w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-50 dark:placeholder:text-zinc-500';
const labelClass = 'text-xs font-medium text-zinc-600 dark:text-zinc-400';
const buttonClass =
  'w-full rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-zinc-50 transition-colors hover:bg-zinc-700 disabled:opacity-60 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300';

const MIN_PASSWORD_LENGTH = 12;

function messageOf(error: unknown): string {
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === 'string' && message.length > 0) return message;
  }
  return 'Sign-in failed. Please try again.';
}

export function LoginForm({
  bootstrap,
  oidc,
  oidcLabel,
  next,
}: {
  bootstrap: boolean;
  oidc: boolean;
  oidcLabel: string;
  next?: string;
}) {
  const router = useRouter();
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** Where to land after signing in — app-relative, so the router prefixes it. */
  const destination = next && next.startsWith('/') ? next : '/';

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (bootstrap && password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
    if (bootstrap && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Choose a password of at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }

    setBusy(true);
    const result = bootstrap
      ? await authClient.signUp.email({ name, email, password })
      : await authClient.signIn.email({ email, password });

    if (result.error) {
      setError(messageOf(result.error));
      setBusy(false);
      return;
    }

    // refresh() re-runs the layout, which is what puts the user chip and the
    // role-aware nav in place before the destination page renders.
    router.push(destination);
    router.refresh();
  }

  async function onSso() {
    setError(null);
    setBusy(true);
    // Absolute path including the basePath: better-auth hands this straight
    // back as a Location header after the IdP round trip.
    const result = await authClient.signIn.oauth2({
      providerId: 'oidc',
      callbackURL: `${BASE_PATH}${destination === '/' ? '/' : destination}`,
    });
    if (result?.error) {
      setError(messageOf(result.error));
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {bootstrap ? 'Create the first account' : 'Sign in'}
        </h1>
        <p className="text-sm text-zinc-600 dark:text-zinc-400">
          {bootstrap
            ? 'This account becomes the administrator, and public registration closes afterwards. Every further account is created here by an admin or provisioned by your identity provider.'
            : 'modelfit'}
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-4">
        {bootstrap && (
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="name">
              Name *
            </label>
            <input
              id="name"
              name="name"
              required
              autoComplete="name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              className={inputClass}
            />
          </div>
        )}

        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="email">
            Email *
          </label>
          <input
            id="email"
            name="email"
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            className={inputClass}
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className={labelClass} htmlFor="password">
            Password *
          </label>
          <input
            id="password"
            name="password"
            type="password"
            required
            minLength={bootstrap ? MIN_PASSWORD_LENGTH : undefined}
            autoComplete={bootstrap ? 'new-password' : 'current-password'}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className={inputClass}
          />
          {bootstrap && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              At least {MIN_PASSWORD_LENGTH} characters.
            </p>
          )}
        </div>

        {bootstrap && (
          <div className="flex flex-col gap-1">
            <label className={labelClass} htmlFor="confirm">
              Repeat password *
            </label>
            <input
              id="confirm"
              name="confirm"
              type="password"
              required
              autoComplete="new-password"
              value={confirm}
              onChange={(event) => setConfirm(event.target.value)}
              className={inputClass}
            />
          </div>
        )}

        {error && (
          <p className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            {error}
          </p>
        )}

        <button type="submit" disabled={busy} className={buttonClass}>
          {bootstrap ? 'Create administrator account' : 'Sign in'}
        </button>
      </form>

      {oidc && !bootstrap && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3 text-xs uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
            or
            <span className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
          </div>
          <button
            type="button"
            onClick={onSso}
            disabled={busy}
            className="w-full rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:opacity-60 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            {oidcLabel}
          </button>
        </div>
      )}
    </div>
  );
}
