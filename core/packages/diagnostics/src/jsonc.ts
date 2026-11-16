/** Parses JSON with comments and trailing commas (the wrangler.jsonc dialect). Comment markers inside strings are kept. */
export function parseJsonc(text: string): any {
  let out = "";
  let i = 0;
  let inString = false;
  while (i < text.length) {
    const c = text[i] as string;
    const n = text[i + 1];
    if (inString) {
      out += c;
      if (c === "\\") {
        out += n ?? "";
        i += 2;
        continue;
      }
      if (c === '"') inString = false;
      i += 1;
      continue;
    }
    if (c === '"') {
      inString = true;
      out += c;
      i += 1;
    } else if (c === "/" && n === "/") {
      while (i < text.length && text[i] !== "\n") i += 1;
    } else if (c === "/" && n === "*") {
      i += 2;
      while (i < text.length && !(text[i] === "*" && text[i + 1] === "/")) i += 1;
      i += 2;
    } else {
      out += c;
      i += 1;
    }
  }
  return JSON.parse(out.replace(/,(\s*[}\]])/g, "$1"));
}

export interface DeployIds {
  databaseId: string;
  storeId: string;
  gatewayAccount?: string;
  gatewayId?: string;
  workerName?: string;
}

/** Fills the committed placeholder ids with the user's real ones. The result is never committed. */
export function renderDeployConfig(base: any, ids: DeployIds): any {
  const cfg = structuredClone(base);
  cfg.workers_dev = true;
  cfg.preview_urls = false;
  if (ids.workerName) cfg.name = ids.workerName;
  for (const d of cfg.d1_databases ?? []) d.database_id = ids.databaseId;
  for (const s of cfg.secrets_store_secrets ?? []) s.store_id = ids.storeId;
  cfg.vars = { ...(cfg.vars ?? {}), AI_GATEWAY_ACCOUNT: ids.gatewayAccount ?? "", AI_GATEWAY_ID: ids.gatewayId ?? "" };
  return cfg;
}
