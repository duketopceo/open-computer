// Tells the Workers types what the main module exports, so `ctx.exports.OutboundProxy(...)` is typed.
export {};

declare global {
  namespace Cloudflare {
    interface GlobalProps {
      mainModule: typeof import("./worker.ts");
    }
  }
}
