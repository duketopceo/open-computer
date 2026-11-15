import type { ProviderRequest } from "../provider/types.ts";

export interface ModelPrice {
  /** Dollars per million input tokens. */
  inPerMTok: number;
  /** Dollars per million output tokens. */
  outPerMTok: number;
}

export interface PriceBook {
  price(model: string): ModelPrice | null;
}

/** Used when a model's price is unknown: deliberately high so an unknown model reserves conservatively. */
export const CONSERVATIVE_PRICE: ModelPrice = { inPerMTok: 15, outPerMTok: 75 };
export const DEFAULT_MAX_OUTPUT_TOKENS = 4096;

export function staticPriceBook(table: Record<string, ModelPrice>): PriceBook {
  return { price: (model) => table[model] ?? null };
}

export interface CostEstimate {
  usd: number;
  /** True when the model's price was unknown and the conservative default was used. */
  estimated: boolean;
}

/** Prompt tokens approximated at four characters each, plus the full output limit at the output price. */
export function estimateCost(request: ProviderRequest, book: PriceBook): CostEstimate {
  const promptChars = JSON.stringify(request.messages).length + JSON.stringify(request.tools ?? []).length;
  const promptTokens = Math.ceil(promptChars / 4);
  const outTokens = request.maxTokens ?? DEFAULT_MAX_OUTPUT_TOKENS;
  const known = book.price(request.model);
  const price = known ?? CONSERVATIVE_PRICE;
  const usd = (promptTokens * price.inPerMTok + outTokens * price.outPerMTok) / 1_000_000;
  return { usd, estimated: known === null };
}
