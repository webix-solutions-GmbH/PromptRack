'use client';

import { useRouter } from 'next/navigation';
import { ToolsetForm } from './toolset-form';

/**
 * Wraps {@link ToolsetForm} for the create case: the page passes the server
 * action in, and a successful create refreshes the list in place.
 */
export function CreateToolsetForm({
  action,
}: {
  action: (formData: FormData) => Promise<void>;
}) {
  const router = useRouter();

  return (
    <ToolsetForm
      initialValues={{ name: '', description: '', kind: 'manual', mcpUrl: '', mcpHeaders: '' }}
      submitLabel="Create toolset"
      onSubmit={async (formData) => {
        await action(formData);
        router.refresh();
      }}
    />
  );
}
