import { Icon } from "@/components/icons/Icon";

/** Always visible in the demo and not dismissable: nobody should mistake sample data for their own. */
export function DemoBanner() {
  return (
    <div
      role="note"
      data-testid="demo-banner"
      className="flex shrink-0 items-center justify-center gap-2 border-b border-line bg-attention-soft px-4 py-2 text-center text-sm text-ink"
    >
      <Icon name="step" />
      <span>
        <strong className="font-semibold">Demo.</strong> Sample data only. Nothing runs and nothing is sent; changes reset when you reload.
      </span>
    </div>
  );
}
