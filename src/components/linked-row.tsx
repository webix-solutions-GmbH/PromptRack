'use client';

import { useRouter } from 'next/navigation';

/**
 * A table row that navigates on click, so the whole row acts as the link.
 * Clicks on real interactive elements inside the row (links, buttons, form
 * controls) are left alone — the delete button must not open the detail page.
 */
export function LinkedRow({
  href,
  className,
  children,
}: {
  href: string;
  className?: string;
  children: React.ReactNode;
}) {
  const router = useRouter();

  function handleClick(event: React.MouseEvent<HTMLTableRowElement>) {
    const target = event.target as HTMLElement;
    if (target.closest('a, button, input, textarea, select, summary, label')) return;
    router.push(href);
  }

  return (
    <tr onClick={handleClick} className={`cursor-pointer ${className ?? ''}`}>
      {children}
    </tr>
  );
}
