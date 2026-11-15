export const DEFAULT_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024;

export class ArtifactError extends Error {
  readonly code: "too_large" | "bad_name";
  constructor(code: "too_large" | "bad_name", message: string) {
    super(message);
    this.name = "ArtifactError";
    this.code = code;
  }
}

function keyFor(taskId: string, name: string): string {
  if (!name || name.startsWith("/") || name.split("/").includes("..") || name.includes("\0")) {
    throw new ArtifactError("bad_name", "Artifact names must be relative paths without '..'.");
  }
  return `tasks/${taskId}/${name}`;
}

/** Files a task produced, stored under a per-task prefix with a size cap. */
export class R2Artifacts {
  private readonly bucket: R2Bucket;
  private readonly maxBytes: number;
  constructor(bucket: R2Bucket, maxBytes = DEFAULT_MAX_ARTIFACT_BYTES) {
    this.bucket = bucket;
    this.maxBytes = maxBytes;
  }

  async put(taskId: string, name: string, body: ArrayBuffer | string): Promise<string> {
    const size = typeof body === "string" ? new TextEncoder().encode(body).byteLength : body.byteLength;
    if (size > this.maxBytes) throw new ArtifactError("too_large", `The file is ${size} bytes; the limit is ${this.maxBytes}.`);
    const key = keyFor(taskId, name);
    await this.bucket.put(key, body);
    return key;
  }

  async get(taskId: string, name: string): Promise<ArrayBuffer | null> {
    const obj = await this.bucket.get(keyFor(taskId, name));
    return obj ? obj.arrayBuffer() : null;
  }

  async list(taskId: string): Promise<string[]> {
    const prefix = `tasks/${taskId}/`;
    const res = await this.bucket.list({ prefix });
    return res.objects.map((o) => o.key.slice(prefix.length));
  }
}
