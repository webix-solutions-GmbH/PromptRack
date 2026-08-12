import { isFirstAccount, oidcButtonLabel, oidcConfigured } from '@/lib/auth';
import { LoginForm } from '@/components/auth/login-form';

export const dynamic = 'force-dynamic';

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const { next } = await searchParams;
  const bootstrap = await isFirstAccount();

  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-sm rounded-lg border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
        <LoginForm
          bootstrap={bootstrap}
          oidc={oidcConfigured()}
          oidcLabel={oidcButtonLabel()}
          next={next}
        />
      </div>
    </div>
  );
}
