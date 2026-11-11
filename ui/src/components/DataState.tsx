import { Icon } from "./icons/Icon";
import { Button } from "./primitives";
import { Skeleton } from "./Skeleton";

/** Loading / error states shared by every data page (the empty state is EmptyState). Renders nothing once data is present
 *  and there is no error, so pages put it above their content. */
export function DataState({
  loading,
  error,
  hasData,
  onRetry,
}: {
  loading: boolean;
  error: string;
  hasData: boolean;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-urgent bg-urgent-soft px-3 py-2 text-sm text-urgent" role="alert">
        <Icon name="failed" />
        <span>{error}</span>
        <Button variant="subtle" className="h-7 max-md:h-10" onClick={onRetry}>
          <Icon name="retry" />
          Retry
        </Button>
      </div>
    );
  }
  if (loading && !hasData) {
    return (
      <div role="status" aria-live="polite">
        <p className="mb-3 text-sm text-muted">Loading…</p>
        <Skeleton rows={3} />
      </div>
    );
  }
  return null;
}
