// Mirrors src/synthesis/vocab.py (SHOCK_FIELDS) — the AITrade scenario-synthesis
// contract. Used to order/group the shock-vector table and render the sign/unit
// legend. Fields returned by the API that aren't here still render (defensively),
// but the backend rejects unknown fields/units, so that should be rare.

export interface ShockSpec {
  field: string;
  units: string[];
  preferred: string;
  positiveMeans: string;
  group: ShockGroup;
}

export type ShockGroup = "Rates" | "Credit" | "Equity" | "Volatility" | "FX";

export const SHOCK_FIELDS: ShockSpec[] = [
  { field: "UST_2Y", units: ["bp"], preferred: "bp", positiveMeans: "yields rise", group: "Rates" },
  { field: "UST_5Y", units: ["bp"], preferred: "bp", positiveMeans: "yields rise", group: "Rates" },
  { field: "UST_10Y", units: ["bp"], preferred: "bp", positiveMeans: "yields rise", group: "Rates" },
  { field: "UST_30Y", units: ["bp"], preferred: "bp", positiveMeans: "yields rise", group: "Rates" },
  { field: "UST", units: ["bp"], preferred: "bp", positiveMeans: "yields rise (parallel shift)", group: "Rates" },
  // rate decomposition (spec vocab-rates-decomp): FRED DFII10 + NY Fed ACM 10Y
  { field: "REAL_YIELD_10Y", units: ["bp"], preferred: "bp", positiveMeans: "real yield rises (10Y TIPS)", group: "Rates" },
  { field: "TERM_PREMIUM_10Y", units: ["bp"], preferred: "bp", positiveMeans: "term premium rises (ACM 10Y)", group: "Rates" },
  { field: "IG_OAS", units: ["bp"], preferred: "bp", positiveMeans: "spreads widen", group: "Credit" },
  { field: "HY_OAS", units: ["bp"], preferred: "bp", positiveMeans: "spreads widen", group: "Credit" },
  { field: "XCCY_BASIS", units: ["bp"], preferred: "bp", positiveMeans: "—", group: "Credit" },
  { field: "EQUITY_US", units: ["pct"], preferred: "pct", positiveMeans: "equity index rises", group: "Equity" },
  { field: "MOVE", units: ["rel", "pts"], preferred: "rel", positiveMeans: "rates vol rises (aggregate/parallel)", group: "Volatility" },
  { field: "MOVE_1Y", units: ["rel", "pts"], preferred: "rel", positiveMeans: "rates vol rises (~1Y expiry)", group: "Volatility" },
  { field: "MOVE_5Y", units: ["rel", "pts"], preferred: "rel", positiveMeans: "rates vol rises (~5Y expiry)", group: "Volatility" },
  { field: "MOVE_10Y", units: ["rel", "pts"], preferred: "rel", positiveMeans: "rates vol rises (~10Y expiry)", group: "Volatility" },
  { field: "MOVE_30Y", units: ["rel", "pts"], preferred: "rel", positiveMeans: "rates vol rises (~30Y expiry)", group: "Volatility" },
  { field: "VIX", units: ["pts"], preferred: "pts", positiveMeans: "equity vol rises", group: "Volatility" },
  { field: "USDTWD", units: ["pct"], preferred: "pct", positiveMeans: "TWD depreciates", group: "FX" },
  { field: "USDJPY", units: ["pct"], preferred: "pct", positiveMeans: "JPY depreciates", group: "FX" },
];

export const SHOCK_GROUPS: ShockGroup[] = ["Rates", "Credit", "Equity", "Volatility", "FX"];

const BY_FIELD = new Map(SHOCK_FIELDS.map((s) => [s.field, s]));

export function shockSpec(field: string): ShockSpec | undefined {
  return BY_FIELD.get(field);
}

export function shockGroupOf(field: string): ShockGroup | "Other" {
  return BY_FIELD.get(field)?.group ?? "Other";
}

// Field-validation codes (mirrors HARD_VALIDATION_CODES + the code table in
// src/synthesis/validate.py / guide 05 §Field validation). Hard codes are dropped
// when synthesis.drop_invalid is on; the rest are surface-only flags. Unknown codes
// (e.g. a new coherence rule) render as soft with no description.
export const HARD_VALIDATION_CODES = new Set([
  "implausible_magnitude",
  "unit_scale_mismatch",
  "horizon_mismatch",
  "direction_mismatch",
  "card_value_mismatch",
]);

const VALIDATION_HINT: Record<string, string> = {
  implausible_magnitude: "move exceeds the horizon-scaled plausibility band",
  unit_scale_mismatch: "value copies a cited number's digits across a unit change",
  exceeds_observed_range: "move dwarfs the hydrated series' observed span",
  horizon_mismatch: "evidence covers a very different horizon than requested",
  horizon_unknown: "no horizon stated for the evidence",
  direction_mismatch: "sign contradicts the requested direction",
  card_value_mismatch: "does not carry the cited episode card's largest move",
  fx_proxy_unlisted: "FX analog anchored only on off-list currencies",
  episode_mixed: "vector cites more than one market-episode card",
  rates_vol_term_inconsistent: "rates-vol term structure disagrees in sign",
};

export function isHardCode(code: string): boolean {
  return HARD_VALIDATION_CODES.has(code);
}

export function validationHint(code: string): string | undefined {
  return VALIDATION_HINT[code];
}

// stable display order: vocab order first, then any unknown fields alphabetically
export function orderShockFields(fields: string[]): string[] {
  const known = SHOCK_FIELDS.map((s) => s.field).filter((f) => fields.includes(f));
  const unknown = fields.filter((f) => !BY_FIELD.has(f)).sort();
  return [...known, ...unknown];
}
