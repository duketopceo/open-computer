// Key-shaped test values are assembled at run time so no source line looks like a real provider key to a
// secret scanner. They are not secrets: the redaction and environment-guard tests need the shape, not a key.
export const fakeOpenRouterKey = (tail = "abcdef1234567890"): string => ["sk", "or", "v1", tail].join("-");
export const fakeGroqKey = (): string => ["gsk", "000000000"].join("_");
export const fakeCloudflareToken = (): string => ["cf", "token", "000000"].join("-");
export const fakeGenericKey = (): string => ["sk", "test", "KEY-should-never-leak"].join("-");
