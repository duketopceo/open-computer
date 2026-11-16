/**
 * The main process of every sandbox: it does nothing but wait, and exits 0 on SIGTERM. Cloudflare ends an instance with SIGTERM
 * (a rollout, the inactivity timeout, our own stop), and `sleep infinity` as the main process ignores it (it is process 1), so
 * the instance would be killed and shown as failed. With this one the stop is an exit with code 0.
 */
export const MAIN_PROCESS: string[] = ["sh", "-c", "trap 'exit 0' TERM INT; sleep infinity & wait $!"];
