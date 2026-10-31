import { PageShell } from "@/components/PageShell";
import { Link } from "@/lib/router";

export default function NotFound() {
  return (
    <PageShell title="Not found">
      <p className="text-sm text-muted">There is no console page at this address.</p>
      <Link href="/" className="mt-3 inline-block text-sm font-medium text-teal underline underline-offset-2">
        Back to the computer
      </Link>
    </PageShell>
  );
}
