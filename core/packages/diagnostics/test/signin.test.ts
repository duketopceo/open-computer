import { describe, expect, it } from "vitest";
import { createControlPlane, type Runner } from "../src/control.ts";
import { NOT_AVAILABLE_BY_OAUTH, parseWhoami, REQUIRED_SCOPES, signInArgs } from "../src/signin.ts";

const ID = "a".repeat(32);
const SIGNED_IN = `
 ⛅️ wrangler 4.162.0
👋 You are logged in with an OAuth Token, associated with the email someone@example.com.
┌────────────────────────────────┬──────────────────────────────────┐
│ Account Name                   │ Account ID                       │
├────────────────────────────────┼──────────────────────────────────┤
│ Someone's Account              │ ${ID} │
└────────────────────────────────┴──────────────────────────────────┘
🔓 Token Permissions:
Scope (Access)
- user (read)
- account (read)
- workers (write)
- workers_scripts (write)
- workers_routes (write)
- workers_tail (read)
- d1 (write)
- secrets_store (write)
- containers (write)
- cloudchamber (write)
`;
const NOT_SIGNED_IN = "Getting User settings...\nYou are not authenticated. Please run `wrangler login`.";

describe("what the sign-in asks for", () => {
  it("is a browser login with the exact scopes setup needs, and no token anywhere", () => {
    const args = signInArgs();
    expect(args.slice(0, 2)).toEqual(["login", "--scopes"]);
    expect(args.slice(2)).toEqual([...REQUIRED_SCOPES]);
    expect(args.join(" ")).not.toMatch(/api.?token|password|--key/i);
  });

  it("says plainly which things OAuth cannot grant", () => {
    expect(NOT_AVAILABLE_BY_OAUTH.join(" ")).toMatch(/AI Gateway/);
    expect(NOT_AVAILABLE_BY_OAUTH.join(" ")).toMatch(/Access/);
  });
});

describe("parseWhoami", () => {
  it("reads the account name, the email and the granted scopes, and never the account id", () => {
    const s = parseWhoami(SIGNED_IN);
    expect(s).toMatchObject({ signedIn: true, email: "someone@example.com", accounts: ["Someone's Account"], missing: [] });
    expect(s.scopes).toContain("containers");
    expect(JSON.stringify(s)).not.toContain(ID);
  });

  it("reports not signed in, and the scopes that are missing from a partial sign-in", () => {
    expect(parseWhoami(NOT_SIGNED_IN).signedIn).toBe(false);
    const partial = parseWhoami(SIGNED_IN.replace("- containers (write)\n", "").replace("- d1 (write)\n", ""));
    expect(partial.missing).toEqual(["d1:write", "containers:write"]);
  });

  it("treats an API token from the environment (Advanced / CI) as enough", () => {
    expect(parseWhoami("", true).missing).toEqual([]);
  });
});

describe("step 1 of the chain", () => {
  const plane = (whoami: string) => {
    const run: Runner = async (args) => (args[0] === "whoami" ? { code: 0, out: whoami } : { code: 0, out: "" });
    return createControlPlane({ run, cloudDir: "/nonexistent" });
  };

  it("shows who is signed in and what was granted when everything is there", async () => {
    const out = await plane(SIGNED_IN).run("account");
    expect(out.ok).toBe(true);
    expect(out.metrics?.find((m) => m.label === "signed in as")?.value).toBe("someone@example.com");
    expect(out.metrics?.find((m) => m.label === "account")?.value).toBe("Someone's Account");
    expect(out.metrics?.find((m) => m.label === "access granted")?.value).toContain("workers_scripts");
    expect(JSON.stringify(out)).not.toContain(ID);
  });

  it("asks the person to click Sign in with Cloudflare, with no token step", async () => {
    const out = await plane(NOT_SIGNED_IN).run("account");
    expect(out.ok).toBe(false);
    expect(out.fix?.action).toBe("sign-in");
    expect(out.fix?.text).toContain("click Allow");
    expect(out.fix?.text).not.toMatch(/create a token|paste/i);
  });

  it("names what is missing after a partial sign-in and offers to sign in again", async () => {
    const out = await plane(SIGNED_IN.replace("- containers (write)\n", "")).run("account");
    expect(out.ok).toBe(false);
    expect(out.detail).toContain("containers:write");
    expect(out.fix?.action).toBe("sign-in");
  });
});
