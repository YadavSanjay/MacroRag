# synth-analog-quality — catch unit/scale slips and off-target FX proxies in shock fields

## Requirement

The validation pass must flag two deterministic errors that the citation gate and the existing
magnitude checks miss, before a consumer prices the number:

1. **Unit/scale slip.** The model copies a cited number's digits but not its unit. Live case
   (faultline Taiwan-blockade question, 2026-10-12, corpus `macro-2026-10-06-feffca9a178f`):
   IMF GFSR chunk `imf_gfsr_geopolitical_2025-04-22_ch2.pdf::008` says CDS spreads "increase
   more than 1 percent", and the model emitted `IG_OAS = +1 bp`. 1 percent is 100bp. The
   existing plausibility band only flags moves that are too large, so a tiny one passes.
2. **Off-target FX proxy.** In the same run, `USDTWD = +5 pct` (analog) was anchored on "USD/UAH
   rose about 5 percent". The hryvnia is not a reasonable proxy for the Taiwan dollar.

Both checks follow the existing contract of `synthesis/validate.py`: **annotate, don't drop**.
A hard code is removed only under `synthesis.drop_invalid`.

Out of scope: judging whether an analog *episode* fits (only the FX proxy currency is checked);
proxies for non-FX fields; LLM-based checks.

## Definitions

- **Cited text** of a field: the concatenated text of the chunks whose `chunk_id` the field
  cites. Chunk ids that don't resolve contribute nothing.
- **Cited quantity:** a match in cited text of a number followed by a unit token. The number is
  optional `-`/`+`, digits, an optional `.digits`. Then optional spaces, then one of the unit
  tokens below, case-insensitive, the longest match first:

  | Unit token | Kind |
  |---|---|
  | `percentage point`, `percentage points`, `pp` | `pp` |
  | `percent`, `per cent`, `%` | `percent` |
  | `basis point`, `basis points`, `bp`, `bps` | `bp` |
  | `index point`, `index points`, `point`, `points`, `pts` | `points` |

  A word token must end at a word boundary. A number with no unit token is not a quantity.
- **Conversion factor** from a quantity kind to a field unit. Undefined pairs mean that
  quantity is ignored for that field.

  | Field unit | `percent` | `pp` | `bp` | `points` |
  |---|---|---|---|---|
  | `bp` | 100 | 100 | 1 | — |
  | `pct` | 1 | 1 | 0.01 | — |
  | `pts` | — | — | — | 1 |
  | `rel` | 0.01 | 0.01 | — | — |

## Acceptance criteria

- **C1**: `synthesis.validate.check_analog_quality(fields, chunks, diag=diag)` adds the code
  `unit_scale_mismatch` to a field's `validation` list exactly when **both** hold:
  - **(a)** some cited quantity `q` (kind `k`) has `|q| = |value|` (relative tolerance 1e-6), and
    the factor `k → unit` is defined and ≠ 1;
  - **(b)** no cited quantity `q'` with a defined factor `f'` has `| |q'|·f' − |value| | ≤
    0.05·|value|`.

  When it flags, it also records **one**
  `diag.warn("unit_scale_mismatch", field=<name>, value=<value>, unit=<unit>, cited=<|q|>,
  cited_unit=<k>)` for the field, where `q` is the first cited quantity meeting (a). The code
  appears once in `validation`. It applies to every field, direct or analog.
  Exact code membership. Oracle: the definitions applied by hand, e.g. `IG_OAS` 1 bp with
  cited "more than 1 percent" flags; 100 bp with the same text does not; `EQUITY_US` −10 pct
  with "10 percent" does not; `VIX` 10 pts with "10 percent" does not (no factor percent → pts).
- **C2**: For a field whose `grounding_mode` is `analog` and whose name is in the allowlist
  below, `check_analog_quality` adds `fx_proxy_unlisted` exactly when the field's `rationale`
  names at least one currency code from the recognised set, other than `USD`, **and none** of
  the named codes is in the field's allowlist. It records
  one `diag.warn("fx_proxy_unlisted", field=<name>, named=<sorted named codes other than USD>)`.
  A currency code is a
  whole-word, upper-case match, including inside pairs like `USD/UAH`.
  - **Allowlist:** `USDTWD`: TWD, KRW, CNY, CNH, SGD, HKD, MYR, THB, PHP, IDR, INR.
    `USDJPY`: JPY, CHF.
  - **Recognised codes:** the allowlist codes plus USD, EUR, GBP, AUD, NZD, CAD, NOK, SEK, MXN,
    BRL, ZAR, TRY, RUB, UAH, PLN, HUF, CZK, ILS.

  A rationale that names no recognised code, or a direct field, is never flagged. Exact code
  membership. Oracle: the allowlist applied by hand ("USD/UAH rose 5 percent" flags; "KRW and
  TWD weakened" does not; "the currency fell" does not).
- **C3**: `QueryPipeline.synthesize` runs `check_analog_quality` over the gated fields
  whenever `synthesis.numeric_validation` is on. So:
  - the response's `shock_vector[<f>].validation` and `synthesis_diagnostics.validation` carry
    these codes;
  - "the response" is `api.models.synthesis_to_dict` of the `SynthesisResult`, which REST and MCP
    both return;
  - `unit_scale_mismatch` is a **hard** code (it joins `HARD_VALIDATION_CODES`, and the check
    runs before the existing `drop_invalid` pass): with `synthesis.drop_invalid = True` the field is
    absent from the shock vector, and `synthesis_diagnostics.dropped` has an entry for it with
    `stage == "validate"` whose `codes` include `unit_scale_mismatch`;
  - `fx_proxy_unlisted` is surface-only: the field is kept even with `drop_invalid`;
  - with `numeric_validation = False`, neither code appears.

  Exact membership. Oracle: a scripted `FakeLLM` that emits the live case's two fields over a
  fixture chunk with the GFSR sentence, applied to the rules above.

## Interface

- `synthesis.validate.check_analog_quality(fields: dict[str, ShockField], chunks:
  list[RetrievedChunk], *, diag: SynthesisDiagnostics | None = None) -> None` — new. It annotates
  `fields` in place and never drops.
- `pipelines.query_pipeline.QueryPipeline.synthesize(...)` — existing, behaviour changes (C3).

## Notes

- Fixtures are synthetic text chunks (`utils.schema.RetrievedChunk` with dated payloads),
  `ShockField` instances, and, for C3, the in-memory pipeline with the hashing embedder and
  `FakeLLM`. No `data/`, network or clock.
- The allowlist and recognised codes are data the implementation adds to `synthesis/vocab.py`.
  This spec fixes their contents.
- Live check after implementation (not a locked test): on the Taiwan question, IG_OAS +1bp
  carries `unit_scale_mismatch`, and USDTWD from a UAH anchor carries `fx_proxy_unlisted`.
