# MacroRAG — Evaluation Questions · Viewpoint B: Stress-Testing Backend

**Persona:** The faultline stress engine (Methodology A/B) — or an analyst driving the
scenario-synthesis endpoint — using MacroRAG as the backend that turns retrieved macro
narrative + policy catalysts into a **structured, citation-gated shock vector** for an
AI-themed derivative portfolio (30 trades across MAG-7 equity/options, UST bonds,
SOFR swaps/swaptions/caps, USD/TWD CCS, USD/JPY forward, and IG/single-name CDS).

**Output contract these questions target:** a shock vector over the canonical **17** fields —
`UST_2Y / UST_5Y / UST_10Y / UST_30Y / UST(parallel)` (bp, +=yields rise),
`IG_OAS / HY_OAS` (bp, +=widen), `MOVE` (rel/pts, +=rates-vol up) plus the
`MOVE_1Y / MOVE_5Y / MOVE_10Y / MOVE_30Y` rates-vol **term structure** (same units/sign, by
swaption expiry), `VIX` (pts, +=equity-vol up), `EQUITY_US` (pct, +=index **rises** — a
broad equity *level*, not vol), `USDTWD` (pct, +=TWD depreciates), `USDJPY` (pct, +=JPY
depreciates), `XCCY_BASIS` (bp) — each field **signed, with a unit, cited to dated
chunk_ids, and a `grounding_mode` of `direct` (evidence quantifies the factor for the
scenario) or `analog` (proxy anchored to the closest historical / official adverse-scenario
path).** Fields with too few citation chunks are gated out rather than invented.

> Canonical list: `src/synthesis/vocab.py`, tabulated in
> [guide 05 §5](../docs/guides/05-api-and-integration.md). The `MOVE_*` nodes and
> `EQUITY_US` were added after most of these questions were written, so a question phrased
> around the original twelve still runs correctly — it simply exercises a subset.

**Trade-map shorthand** used below (from the source catalog §1): #17 ORCL 2053 / #18 META
2054 (long-end corporate), #20 UST 10Y / #21 UST bond, #23 5y10y payer swaption / #24–25
caps, #26 USD/TWD CCS, #27 USD/JPY forward, #28–30 IG / single-name CDS, #2 TSM-ADR /
#3 AVGO / #4 MU (semis).

These questions are medium-to-moderately-complex: each defines a scenario or factor
channel and expects a reasoned, cited, magnitude-bearing answer suitable for the
QuantLib revaluation handoff — not a single fact. They exercise sign/unit correctness,
the citation gate and coverage-gap signal, `direct`/`analog` grounding, multi-hop causal
decomposition, world_view_id provenance stamping, and the quarterly-definition /
weekly-application cadence.

---

## Single-channel shock proposals (direct grounding)

1. Given the latest FOMC statement and SEP, propose a signed shock to `UST_2Y`,
   `UST_5Y`, `UST_10Y` for a "Fed drops its easing bias" scenario over a 3-month
   horizon, with cited rationale per field.

2. From the most recent Quarterly Refunding + TBAC materials, size a term-premium
   shock to `UST_30Y` (and `UST_10Y`) for a "heavier-than-expected long-end issuance"
   scenario. Justify the magnitude against the cited financing estimates.

3. Using the Fed FSR and IMF GFSR valuation-stretch language, propose a `VIX` shock
   for a "valuations re-rate lower" scenario, and state whether the grounding is
   `direct` or `analog`.

4. From SLOOS + IMF GFSR + the house-view credit framing, propose `IG_OAS` and
   `HY_OAS` shocks for a "credit conditions tighten as AI issuance floods the market"
   scenario.

5. Based on FOMC forward-guidance posture and Treasury issuance uncertainty, propose a
   `MOVE` (rates-vol) shock for a "guidance-driven rate-vol spike" scenario.

6. From the BoJ Outlook Report, propose a `USDJPY` shock for a "BoJ normalizes faster
   than expected" scenario, with the cited passage anchoring the magnitude.

7. From the BIS Quarterly/Annual funding narrative + Treasury TIC, propose an
   `XCCY_BASIS` shock for a "USD funding squeeze" scenario.

8. From the latest CPI/PCE surprise + FOMC reaction-function language, propose a
   front-end repricing shock to `UST_2Y` for an "inflation reaccelerates, cuts get
   priced out" scenario.

9. Using a DFAST severely-adverse macro path as a ready-made template, extract its
   implied rate/credit/equity moves and express them as `UST`, `IG_OAS`, `HY_OAS`,
   `VIX` shock fields (Methodology A).

10. From SIA + TSMC foundry-demand deterioration, propose a semis-equity-vol read and
    the `VIX` leg it implies for an "AI-hardware demand rolls over" scenario.

## Multi-factor / correlated joint scenarios

11. Construct the full joint shock vector for the **Taiwan invasion tail** (6-month
    horizon): `USDTWD`, `IG_OAS`, `HY_OAS`, `VIX`, `MOVE`, `UST` across rates/credit/
    vol/FX. Where no dated chunk quantifies a leg, use Tier-7 analog grounding and mark
    it `analog` — do not invent an ungrounded number.

12. Build the "AI-capex bust" joint scenario (Methodology B early-bust signal): tie a
    data-center-power / interconnection constraint to a hyperscaler-capex cut, and
    propose the equity-vol (`VIX`) and credit (`IG_OAS`/`HY_OAS`) legs it implies.

13. For a "hyperscaler profit disappointment (metaverse moment)" scenario, propose the
    `VIX` + `IG_OAS` legs and explain the concentration channel from the house-view
    citations.

14. Assemble a "stagflationary inflation surprise delays Fed cuts" scenario: signed
    shocks to `UST_2Y`/`UST_5Y` (repricing), `MOVE`, and `VIX`, grounded in the latest
    CPI/PCE + FOMC reaction-function language.

15. Construct the export-control-escalation joint tail (`USDTWD`, `VIX`, `IG_OAS`,
    plus a semis-equity read) triggered by a new BIS-Commerce rule or Section 232 step,
    citing the Federal Register catalyst.

16. Build a "global risk-off / flight-to-quality" scenario where UST *rallies*
    (negative `UST` shock) while `VIX`/`IG_OAS`/`HY_OAS` rise — get the signs right and
    ground the safe-haven leg.

17. Construct a "term-premium blowout" scenario that jointly moves `UST_10Y`, `UST_30Y`,
    and `MOVE` while leaving `UST_2Y` roughly anchored — i.e., a bear-steepener — and
    justify why the front end stays pinned.

18. Build a "BoJ normalization spillover" scenario linking `USDJPY`, `XCCY_BASIS`, and a
    modest `UST` back-up (foreign-demand channel via TIC), and cite the transmission at
    each leg.

19. Construct a combined "power-constraint + export-control" double-hit on the semis
    complex: how do `USDTWD`, `VIX`, and `IG_OAS` compound when both the physical (grid)
    and policy (chip) constraints bind at once?

20. Propose a "credit-led" scenario where `HY_OAS` leads and `IG_OAS` follows with a
    smaller move — justify the IG/HY differential from SLOOS + GFSR + house-view credit
    framing, and note the implied #28–30 CDS impact.

## Analog / official-scenario grounding (Tier 7)

21. Anchor a geopolitically-triggered global-stress shock vector to the EBA/ESRB
    EU-wide adverse macro-financial scenario. Map its quantified GDP/long-rate/equity/
    FX moves onto our shock fields and mark them `analog`.

22. Use the IMF GFSR "geopolitical risks & asset prices" (Apr-2025 Ch.2) event-study to
    size the equity-drawdown and credit-spread legs of a military-conflict scenario.

23. For the Taiwan-conflict legs where no direct number exists, ground magnitudes in the
    St. Louis Fed / Rhodium / CSIS Tier-7 studies and explain why `analog` grounding is
    appropriate here.

24. Compare `direct` vs `analog` grounding for the same `HY_OAS` shock: what would a
    consumer's down-weighting look like, and which cited chunks support each mode?

25. When two Tier-7 analogs (e.g., EBA/ESRB adverse vs IMF GFSR event-study) imply
    different magnitudes for the same `VIX` leg, how should the synthesizer present the
    range, and which analog is the closer fit for a Taiwan-specific event?

26. For a Taiwan blockade (vs. full invasion) sub-scenario, which Tier-7 study is the
    closest analog, and how would you scale the `USDTWD` / `IG_OAS` legs down from the
    invasion case while keeping grounding cited?

## Citation-gate, refusal, and coverage-gap behavior

27. Ask for a shock vector on a scenario the corpus does *not* quantify (e.g., a niche
    single-name event) — confirm the citation gate returns an empty/partial vector with
    a coverage-gap signal rather than fabricating fields.

28. For a proposed shock vector, verify every field carries `chunk_ids`, a `unit`
    matching the field's allowed units, and a sign consistent with the documented
    convention — flag any field that fails the contract.

29. Trigger the historical §7bis failure mode deliberately: request a "Taiwan invasion,
    6-month, rates/credit/vol/FX" vector and confirm Tier-7 analog grounding now fills
    the legs that previously returned empty — and that each is marked `analog`.

30. Ask for a shock field that is *not* in the controlled vocabulary (e.g., a commodity
    or a single-stock beta) and confirm the synthesizer rejects it rather than coercing
    it into a canonical field.

31. Present a scenario where only *some* legs are grounded (e.g., FX cited but credit
    not) — confirm the gate returns the grounded legs and explicitly signals the
    coverage gap on the ungrounded ones rather than guessing.

32. Verify unit discipline: request the `MOVE` leg in both `rel` and `pts` and confirm
    the emitted unit is the preferred one and that the value is internally consistent.

## Provenance, reproducibility, and cadence

33. Synthesize the Taiwan-tail vector and confirm the stored vector RECORDS the
    `world_view_id` it was synthesized under; then re-synthesize after the corpus has been
    re-indexed and confirm the new vector carries a NEW `world_view_id` stamp (and may differ).
    Note that re-querying a *superseded* snapshot is not supported — the guarantee is
    provenance stamping, not frozen re-query against an old corpus (Production Hardening §F).

34. For a term-premium shock, confirm the answer prefers the freshest primary source
    (latest Refunding) and surfaces any conflict with an older TBAC or CBO view rather
    than silently averaging.

35. Given a weekly refresh, identify which stored shock vectors should be re-fired
    unchanged vs. re-synthesized because a new high-frequency catalyst (FOMC week,
    new BIS rule, GPR spike, SIA/TSMC print) materially changed a factor leg.

36. Simulate the quarterly-definition / weekly-application cadence: after a new Quarterly
    Refunding + AEO + earnings-capex cycle, which scenario *definitions* should be
    refreshed, and which legs merely re-apply stored vectors?

37. For a scenario synthesized last quarter, check whether any citation chunk has since
    been superseded by a newer primary release, and describe how the vector should be
    re-grounded if so.

## Factor-channel decomposition & sizing rationale

38. Decompose the discount-rate channel for the long-duration book: how do the FOMC
    path, Treasury issuance, and CBO deficit trajectory each contribute (in bp) to a
    combined `UST_10Y`/`UST_30Y` shock, with per-source attribution?

39. For the credit channel, separate the *market-priced* spread move (`IG_OAS`/`HY_OAS`)
    from the *survey-based* credit-conditions signal (SLOOS) — how should each weight
    into the proposed magnitude?

40. Size the `USDTWD` leg three ways — from OISP/Entity-List catalyst severity, from
    GPR-Index level, and from a Tier-7 Taiwan analog — and reconcile the three estimates
    into a single cited magnitude with an uncertainty band.

41. For the power-constraint → capex → equity chain, quantify the transmission: what
    interconnection/AEO evidence would move the `VIX` leg from a mild to a severe read,
    and cite the thresholds.

42. Run the iterative multi-hop causal decomposition on "a new Section 232 chip tariff":
    hop 1 (foundry/semis demand) → hop 2 (AI-capex cut) → hop 3 (power-demand easing) →
    hop 4 (credit/equity), and attach the shock field(s) each hop implies.

43. Perform horizon sensitivity on the Taiwan tail: how do the `USDTWD` / `VIX` /
    `IG_OAS` magnitudes differ between a 1-month acute-shock horizon and a 6-month
    sustained-stress horizon, and which cited evidence supports each?

44. Show sign-convention correctness on an easing scenario: for a "Fed cuts faster than
    expected" case, confirm `UST` legs go *negative* (yields fall), `VIX` may *fall*, and
    `USDJPY` direction, each with the documented positive-sign convention referenced.

45. Reconcile a conflict where the Fed FSR implies a larger `VIX`/valuation shock than
    the house-view BofA-style positioning read — how should the synthesizer weight
    official vs. sell-side sources when sizing the leg?

46. For the #26 USD/TWD CCS and #27 USD/JPY forward specifically, produce the FX legs
    (`USDTWD`, `USDJPY`) plus the `XCCY_BASIS` leg for a joint "Asia funding stress"
    scenario, and map each to the trade it revalues.

47. Cross-check internal consistency of a full vector: if `MOVE` (rates vol) is shocked
    sharply higher, is the proposed `UST` term-structure move consistent with that vol
    (e.g., a big `UST_30Y` move but tiny `MOVE` should be flagged as incoherent)?

48. Given the faultline Methodology-A historical-replay use case, retrieve the
    contemporaneous Fed FSR / IMF GFSR narrative to *qualitatively* anchor a COVID-2020
    or 2022 credit-episode `IG_OAS`/`HY_OAS` magnitude that FRED no longer serves free —
    and mark the grounding `analog`.

49. Produce a "minimum-viable vs. full" version of the same scenario: the smallest set of
    high-confidence `direct`-grounded legs vs. the fuller vector including `analog` legs —
    so the consumer can choose a coverage/confidence trade-off.

50. Stress the coverage-gap signal end to end: assemble a scenario spanning all 19 fields,
    and for each field report whether it is `direct`, `analog`, or gated-out, with the
    governing citation count — a single audit view of the vector's grounding quality.
