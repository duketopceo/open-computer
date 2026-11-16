import { OPENROUTER_BASE_URL, parsePrices, priceBookFromTable, type FetchLike, type PriceBook, type PriceTable } from "@open-computer/agent-core";

const KEY = "price_book";
const KEY_AT = "price_book_at";
export const PRICE_TTL_MS = 24 * 60 * 60 * 1000;

type Table = PriceTable;
const toBook = priceBookFromTable;
export { parsePrices };

/**
 * The price book from OpenRouter's models endpoint, cached in the settings table and refreshed after a day. A failed
 * refresh keeps the stale copy; with no copy at all every model reserves at the conservative price.
 */
export async function loadPriceBook(db: D1Database, fetchImpl: FetchLike, now: number = Date.now()): Promise<PriceBook> {
  let cached: Table = {};
  let at = 0;
  try {
    const rows = await db.prepare("SELECT key, value FROM settings WHERE key IN (?, ?)").bind(KEY, KEY_AT).all<{ key: string; value: string }>();
    for (const r of rows.results) {
      if (r.key === KEY) cached = JSON.parse(r.value) as Table;
      else at = Number(r.value);
    }
  } catch {
    cached = {};
  }
  if (Object.keys(cached).length > 0 && now - at < PRICE_TTL_MS) return toBook(cached);
  try {
    const res = await fetchImpl(`${OPENROUTER_BASE_URL}/models`, { method: "GET", headers: {}, signal: AbortSignal.timeout(8000) });
    if (res.ok) {
      const fresh = parsePrices(await res.json());
      if (Object.keys(fresh).length > 0) {
        await db.batch([
          db.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(KEY, JSON.stringify(fresh)),
          db.prepare("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value").bind(KEY_AT, String(now)),
        ]);
        return toBook(fresh);
      }
    }
  } catch {
    /* keep the stale copy */
  }
  return toBook(cached);
}
