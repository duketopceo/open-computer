// Writes (or with --check, verifies) core/contracts/events.schema.json from the zod definitions.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { eventsJsonSchema } from "../src/events.ts";

const target = fileURLToPath(new URL("../../../contracts/events.schema.json", import.meta.url));
const text = JSON.stringify(eventsJsonSchema(), null, 2) + "\n";

if (process.argv.includes("--check")) {
  if (readFileSync(target, "utf8") !== text) {
    console.error("contracts/events.schema.json is stale; run: node scripts/gen-schema.ts");
    process.exit(1);
  }
} else {
  writeFileSync(target, text);
}
