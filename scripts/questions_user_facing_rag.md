# MacroRAG — Evaluation Questions · Viewpoint A: User-Facing RAG / UI

**Persona:** A macro/markets analyst using the MacroRAG web UI (or REST/MCP query
endpoint) the way they'd use a Bloomberg, Haver, or a sell-side research portal —
asking open-ended, reasoning-heavy questions inside the system's scope and expecting
grounded, cited prose answers with source attribution and recency handling.

**Scope reminder (what the corpus actually holds):** the *forward-looking, narrative
and policy layer* — Fed (FOMC/SEP/MPR/FSR/SLOOS/DFAST/speeches), Treasury
(Refunding/TBAC/TIC/OISP), US stats (BEA PCE/GDP, BLS CPI/PPI, Census BTOS, CBO),
international (IMF WEO/GFSR, BIS Quarterly/Annual, BoJ Outlook), house views
(JPM EotM/Cembalest, BlackRock BII, GS Top of Mind, MS Thoughts on the Market, Apollo),
AI-theme sources (EIA AEO/STEO, IEA Energy & AI, PJM/FERC, Federal Register BIS-Commerce
rules + Presidential Proclamations, SIA, TSMC, Stanford HAI, GPR/EPU), and Tier-7
cross-asset stress studies. It does **not** re-serve the numeric market series the
pricing pipeline already pulls (FRED OAS, VIX, MOVE, curve, EIA hourly load).

These questions are medium-to-moderately-complex: each requires retrieval across one or
more sources plus reasoning, comparison, or layered synthesis — not a single-fact lookup.
They also intentionally exercise the retriever's harder paths: multi-hop causal
decomposition, temporal deltas across releases, cross-tier triangulation, conflict
surfacing, and recency/time-decay handling.

---

## Monetary policy / rates (Tier 1 Fed, Tier 2 Treasury)

1. How has the FOMC's forward-guidance posture shifted since the Warsh chairmanship
   began, and what does the most recent SEP dot plot imply for the rate path over the
   next four quarters?

2. Reconcile the latest FOMC statement's characterization of inflation progress with
   the most recent BLS CPI and BEA PCE prints — where does the Fed's language lead or
   lag the data?

3. What do the last two sets of FOMC minutes say about the pace of balance-sheet runoff
   (QT), and how might that interact with the long-end supply picture from the most
   recent Treasury Quarterly Refunding?

4. Summarize the term-premium narrative: combine the latest TBAC discussion charts,
   the Quarterly Refunding financing estimates, and CBO's deficit trajectory into a
   view on long-end (10y–30y) issuance pressure.

5. According to the most recent Fed Financial Stability Report, which valuation and
   leverage vulnerabilities are flagged as most acute, and how does that compare with
   the prior report's assessment?

6. What does the latest SLOOS say about lending standards and loan demand, and does it
   confirm or contradict the "AI leveraging-up" credit theme the house views describe?

7. Trace the Fed's evolving stance on r-star / neutral-rate estimates across the most
   recent SEP and any relevant Chair or Governor speeches.

8. How has the dispersion of the SEP dot plot (the spread between the highest and lowest
   dots) changed over the last two projection rounds, and what does widening or
   narrowing dispersion say about Committee consensus?

9. Between the most recent FOMC statement and the subsequent minutes, where did the
   *tone* on the balance of risks shift, and which specific sentences carry that shift?

10. What dissents or "several/some participants" qualifiers appear in the latest minutes,
    and on which policy dimension (rate level, QT pace, guidance) is the Committee least
    unified?

11. Using the most recent Monetary Policy Report and the Chair's accompanying testimony
    framing, how does the Fed characterize the trade-off between labor-market cooling and
    inflation persistence?

12. Walk through the causal chain, step by step, from the latest Quarterly Refunding
    financing estimate to a change in the 30y term premium to the discount rate on a
    27–28-year corporate bond — citing a source at each hop.

## Growth, inflation, fiscal (Tier 3 US stats, Tier 4 IMF)

13. Assemble the current growth picture from BEA GDP components and the most recent
    IMF WEO US forecast — where do the official-agency and Fund views diverge?

14. What is the latest read on services vs. goods inflation from BLS CPI/PPI, and how
    does the Fed's preferred PCE gauge (BEA) frame the same signal?

15. How does CBO's latest Budget & Economic Outlook characterize the deficit and debt
    path, and how is that reflected in Treasury's stated financing needs?

16. The catalog flags shutdown-driven data disruptions. Where in the recent BLS/BEA
    releases is data quality caveated, and how should an analyst weight those prints?

17. Decompose the most recent BEA GDP print into consumption, investment (esp. tech/IP),
    government, and net exports — how much of recent US growth is attributable to the
    AI-capex-heavy investment component per the house-view framing?

18. Compare how the IMF WEO and the sell-side houses each quantify AI's contribution to
    US growth — is AI treated primarily as an upside catalyst or a downside/concentration
    risk, and by whom?

19. What does the latest ECI / productivity data (BLS) imply for unit labor costs, and
    how does that reconcile with the AI-productivity optimism in Stanford HAI and the
    house views?

## AI capex cycle (Tier 5 house views, Tier 3 Census, Tier 6C)

20. Synthesize the hyperscaler AI-capex outlook across BlackRock BII, JPM Eye on the
    Market, and Apollo — where is there consensus, and where do the houses disagree on
    the spend-vs-revenue "financing hump"?

21. What does Cembalest's "Eye on the Market" identify as the medium-term risks to the
    AI trade, and how do those map to the concentration risk in the S&P?

22. Combine Census BTOS AI-adoption readings with the Stanford HAI AI Index to describe
    the current pace of firm-level AI diffusion — is adoption broadening or narrowing?

23. How do the house views frame the risk of a "metaverse moment" (hyperscaler profit
    disappointment) for AI-linked equities, and what evidence do they cite?

24. Trace the "buildout is front-loaded in spend, back-loaded in revenue" thesis to its
    credit-market implication: what do BlackRock, Morgan Stanley, and Apollo each say
    about the resulting bond-issuance / leverage pressure?

25. Across the most recent two rounds of house-view outlooks, how has consensus on
    2026–2028 hyperscaler capex ($/yr trajectory) shifted, and which house is the most
    contrarian relative to that consensus?

26. Reconcile the "official" AI-adoption read (Census BTOS) with the market-narrative
    read (house views): does firm-level adoption data support or temper the capex
    optimism the Street is pricing?

## Power / data-center constraint (Tier 6A)

27. According to EIA AEO and STEO, how much does data-center load contribute to the
    projected US electricity-demand trajectory, and over what horizon?

28. Compare the EIA AEO2026 domestic view with the IEA *Energy and AI* global projection
    for data-center electricity growth — where do the two diverge on magnitude or timing?

29. What do FERC interconnection-queue and PJM materials say about grid bottlenecks in
    "Data Center Alley," and why is interconnection framed as the binding physical cap?

30. Lay out the causal chain from a data-center power constraint to hyperscaler capex
    and AI-equity earnings, using the power sources and house-view framing together.

31. How has the EIA STEO's near-term electricity-demand growth attribution to data
    centers / large computing facilities changed across its most recent monthly updates?

32. Triangulate the data-center power-demand estimate across EIA AEO, IEA, and any
    house-view cross-reference (BlackRock cites IEA/DOE/Berkeley Lab/BNEF/EPRI) — how
    wide is the uncertainty band, and what drives the disagreement?

## Semiconductors / export controls / Taiwan (Tier 6B, Tier 2 OISP)

33. Summarize the current US chip-export-control regime: what did the Jan-2026 BIS
    final rule change (H200 / MI325X treatment), and how does the Section 232
    semiconductor tariff interact with it?

34. What is the latest SIA global semiconductor sales trend and TSMC monthly-sales /
    capex-guidance signal, and what do they jointly imply for near-term AI-hardware
    demand?

35. Which firms and activities does the Treasury OISP (EO-14105) restrict, and how does
    that overlap with the BIS Entity List as a Taiwan-tail policy catalyst?

36. Trace the dated export-control catalyst calendar (e.g., the ~Nov-2026 Affiliates
    Rule resumption) and explain what an analyst should watch on each date.

37. How is a "US data center" defined in the Section 232 semiconductor-tariff carve-out
    (the >100 MW threshold), and why does that definition matter for the tariff's
    effective incidence on domestic AI buildout?

38. Distinguish the Jan-2026 BIS "case-by-case review" posture from the prior
    "presumption of denial" — for the named chips (H200 / MI325X), what conditions
    (supply/security/third-party testing) now gate an export license?

39. Using TSMC monthly sales as a leading indicator, does the most recent foundry-demand
    signal lead or lag the SIA global-sales trend, and what does the divergence (if any)
    imply for the AI-hardware cycle?

40. Enforcement posture: what do the Federal Register / BIS materials say about
    stricter enforcement (e.g., "Operation Gatekeeper", the BIS budget increase), and
    how does that change the credibility of the export-control regime?

## FX / funding / international (Tier 4)

41. What does the latest BoJ Outlook Report signal for policy normalization, and how
    might that feed USD/JPY and the broader carry environment?

42. Combine the BIS Quarterly Review funding narrative with Treasury TIC foreign-demand
    data into a view on USD cross-border liquidity and cross-currency basis pressure.

43. What does the latest TIC data say about the composition of foreign Treasury demand
    (official vs private, by region), and how does that intersect with the term-premium
    story from Tier 1/2?

44. From the BIS Annual Economic Report, summarize the systemic cross-border-USD-liquidity
    risks flagged, and connect them to the funding leg the pricing engine watches.

## Cross-source synthesis, conflict handling, and system meta-queries

45. On the outlook for long-end US rates, where do the Fed, Treasury/TBAC, CBO, and the
    sell-side houses agree, and where do they conflict — surface the disagreement rather
    than collapsing it.

46. Give a single unified "company fundamental + macro context" view for the
    semiconductor complex: pair the export-control / SIA / TSMC macro layer with the
    entity-tagged AI narrative.

47. What is the most recent authoritative source on [data-center power / export controls
    / the rate path], and does any newer source contradict it? Prefer the freshest
    primary source and flag the conflict.

48. Build a "what changed this week" macro briefing: given the most recent releases
    across all tiers, which one or two items most materially update the prior read, and
    why (use recency/time-decay to prioritize)?

49. Where do the four Cembalest medium-term risks (US power, China scaling AI alone,
    Taiwan, hyperscaler-profit "metaverse moment") each find independent corroboration
    in a *non-house-view* primary source (Fed, EIA, IMF, Federal Register)?

50. Identify a case where two sources of different vintage disagree on the same factor
    (e.g., an older IMF WEO growth number vs a newer house-view revision) and explain how
    the system should present the conflict, including which source recency favors.
