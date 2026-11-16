/** Plain-words text for every failure Test connection can report. The UI shows these and never raw errors. */
export const MESSAGES = {
  ok_backend: "The cloud core answered.",
  ok_model: "The model key works.",
  ok_memory: "Memory saved and read back.",
  memory_off: "Memory is switched off.",
  no_cloud_url: "No cloud core address is set yet.",
  no_cloud_secret: "The cloud access secret has not been added yet.",
  backend_unreachable: "The cloud core could not be reached. Check the address and your connection.",
  backend_unauthorized: "The cloud core refused the access secret. Check it was copied exactly.",
  backend_error: "The cloud core answered with an error.",
  backend_timeout: "The cloud core took too long to answer.",
  blocked_by_backend: "This could not be checked because the cloud core is not reachable.",
  model_key_missing: "No model key is stored in the cloud core yet.",
  model_key_rejected: "The model key was rejected. Check it in your model provider account.",
  model_no_credit: "The model provider says the account has no credit.",
  model_rate_limited: "The model provider is limiting requests right now. Try again shortly.",
  model_error: "The model key could not be checked.",
  memory_failed: "Memory could not save and read back a test note.",
} as const;

export type MessageCode = keyof typeof MESSAGES;
