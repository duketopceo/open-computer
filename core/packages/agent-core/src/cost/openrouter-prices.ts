import { staticPriceBook, type ModelPrice, type PriceBook } from "./pricebook.ts";

/** Model id to [dollars per million input tokens, dollars per million output tokens]. */
export type PriceTable = Record<string, [number, number]>;

/** Dollars per million tokens from OpenRouter's per-token strings. Free models keep their stated price of 0. */
export function parsePrices(body: unknown): PriceTable {
  const out: PriceTable = {};
  const data = (body as { data?: unknown })?.data;
  if (!Array.isArray(data)) return out;
  for (const m of data as Array<{ id?: unknown; pricing?: { prompt?: unknown; completion?: unknown } }>) {
    if (typeof m.id !== "string") continue;
    const p = Number(m.pricing?.prompt);
    const c = Number(m.pricing?.completion);
    // Negative values are OpenRouter's marker for "price varies" (routers); leave those unknown so they reserve conservatively.
    if (Number.isFinite(p) && Number.isFinite(c) && p >= 0 && c >= 0) out[m.id] = [p * 1e6, c * 1e6];
  }
  return out;
}

export function priceBookFromTable(t: PriceTable): PriceBook {
  return staticPriceBook(Object.fromEntries(Object.entries(t).map(([id, [i, o]]): [string, ModelPrice] => [id, { inPerMTok: i, outPerMTok: o }])));
}
