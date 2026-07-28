import { RATING_META, type Rating } from '@/lib/rating';

/**
 * The one place a rating is drawn. `showUnrated` renders a neutral "unrated"
 * pill (the compare matrix wants every cell to say something); without it an
 * unrated result draws nothing, which is what the result card wants.
 */
export function RatingBadge({
  rating,
  showUnrated = false,
  className = '',
}: {
  rating: Rating | null;
  showUnrated?: boolean;
  className?: string;
}) {
  if (rating === null) {
    if (!showUnrated) return null;
    return (
      <span
        className={`inline-flex items-center rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400 ${className}`}
      >
        unrated
      </span>
    );
  }

  const meta = RATING_META[rating];

  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${meta.badge} ${className}`}
      title={meta.description}
    >
      {meta.emoji} {meta.label}
    </span>
  );
}
