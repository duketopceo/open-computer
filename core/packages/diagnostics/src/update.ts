// `oc update`: deploy the new core, check its health, and put the previous version back if the check fails.
import type { Runner } from "./control.ts";

export interface UpdateOptions {
  run: Runner;
  /** Rendered deploy config path (see control.ts). */
  config: string;
  workerName: string;
  /** Returns true when the freshly deployed core answers /health with the expected version. */
  healthy: () => Promise<boolean>;
  tries?: number;
  waitMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

export interface UpdateResult {
  ok: boolean;
  /** What happened, in plain words. */
  message: string;
  rolledBackTo?: string;
}

interface Deployment {
  created_on: string;
  versions: Array<{ version_id: string; percentage: number }>;
}

/** The version that serves most traffic in the newest deployment, or null. */
export function currentVersion(listJson: string): string | null {
  try {
    const start = listJson.indexOf("[");
    const all = JSON.parse(listJson.slice(start)) as Deployment[];
    const newest = [...all].sort((a, b) => b.created_on.localeCompare(a.created_on))[0];
    const top = [...(newest?.versions ?? [])].sort((a, b) => b.percentage - a.percentage)[0];
    return top?.version_id ?? null;
  } catch {
    return null;
  }
}

export async function updateCore(o: UpdateOptions): Promise<UpdateResult> {
  const sleep = o.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));
  const before = await o.run(["deployments", "list", "--name", o.workerName, "--json"]);
  const previous = before.code === 0 ? currentVersion(before.out) : null;

  const migrate = await o.run(["d1", "migrations", "apply", "open-computer", "--remote", "-c", o.config]);
  if (migrate.code !== 0) return { ok: false, message: "The database migration failed, so nothing was deployed. Your current version is still running." };

  const deploy = await o.run(["deploy", "-c", o.config]);
  if (deploy.code !== 0) return { ok: false, message: "The deploy failed. Your current version is still running." };

  const tries = o.tries ?? 5;
  for (let i = 0; i < tries; i++) {
    if (await o.healthy()) return { ok: true, message: "Updated. The new version answers its health check." };
    if (i < tries - 1) await sleep(o.waitMs ?? 3000);
  }

  if (!previous) return { ok: false, message: "The new version is not healthy and no previous version was found to go back to. Run `oc doctor` for details." };
  const back = await o.run(["rollback", previous, "-c", o.config, "--message", "oc update: health check failed"]);
  if (back.code !== 0) return { ok: false, message: "The new version is not healthy and the rollback command failed. Roll back in the Cloudflare dashboard (Workers > Deployments)." };
  const restored = await o.healthy();
  return {
    ok: false,
    rolledBackTo: previous,
    message: restored
      ? "The new version failed its health check, so the previous version was restored and is healthy."
      : "The new version failed its health check and the previous version was restored, but it does not answer either. Run `oc doctor`.",
  };
}
