export interface DaemonOptions {
  tokenFile: string;
  port: number;
}

/** Daemon mode is chosen by `--daemon`. Returns null for the normal stdin-supervised sidecar. */
export function parseDaemonOptions(argv: string[], env: Record<string, string | undefined>): DaemonOptions | null {
  if (!argv.includes("--daemon")) return null;
  const tokenFile = env.OC_CORE_TOKEN_FILE;
  if (!tokenFile) throw new Error("OC_CORE_TOKEN_FILE must name the file that holds the access token.");
  const raw = env.OC_CORE_PORT ?? "8787";
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("OC_CORE_PORT must be a port number.");
  return { tokenFile, port };
}
