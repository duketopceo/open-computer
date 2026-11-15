/** The one module that reads Secrets Store bindings (open beta), so a change in the beta's shape touches one file. */

export class SecretError extends Error {
  readonly code = "secret_missing" as const;
  readonly slot: string;
  constructor(slot: string) {
    super(`The secret "${slot}" is not set in the cloud core's secret store.`);
    this.name = "SecretError";
    this.slot = slot;
  }
}

export async function readSecret(binding: { get(): Promise<string> }, slot: string): Promise<string> {
  let value: string | null | undefined;
  try {
    value = await binding.get();
  } catch {
    throw new SecretError(slot);
  }
  if (typeof value !== "string" || value === "") throw new SecretError(slot);
  return value;
}
