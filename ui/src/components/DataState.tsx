import { Button } from "./primitives";

/** Loading / error states shared by every data page. Renders nothing once data is present
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
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
        <span>{error}</span>
        <Button variant="subtle" className="h-7" onClick={onRetry}>
          Retry
        </Button>
      </div>
    );
  }
  if (loading && !hasData) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted" role="status">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-muted/30 border-t-teal" aria-hidden />
        Loading…
      </p>
    );
  }
  return null;
}
