import { useState } from "react";
import { useCitationPrefetch, useFactors } from "@/api/hooks";
import {
  emptyFilter,
  type FactorRelevance,
  type FactorsResultDto,
  type FilterModel,
  type RelevantFactor,
} from "@/api/types";
import {
  FacetFilterEditor,
  activeFilterCount,
} from "@/components/FacetFilterEditor";
import { CitationChip } from "@/components/Citations";
import { EvidenceRail } from "@/components/EvidenceRail";
import { RecentQueries } from "@/components/RecentQueries";
import { RouteBadge } from "@/components/RouteBadge";
import { SavedViews } from "@/components/SavedViews";
import { citationIndex } from "@/lib/citations";
import { useHistory } from "@/lib/persist";
import { submitHint } from "@/lib/platform";

// Business surface for POST /factors — ranked, cited macro risk factors driving a
// portfolio / theme. Complements Ask (prose) and Scenario Studio (shock vector).
export function FactorsPage() {
  const [portfolio, setPortfolio] = useState("");
  const [topK, setTopK] = useState(8);
  const [filters, setFilters] = useState<FilterModel>(emptyFilter());
  const [showFilters, setShowFilters] = useState(false);
  const factors = useFactors();
  const history = useHistory("factorsHistory");

  const nFilters = activeFilterCount(filters);
  const result = factors.data;

  function discover(p: string) {
    const t = p.trim();
    if (!t || factors.isPending) return;
    history.add(t);
    factors.mutate({
      portfolio: t,
      filters: nFilters ? filters : null,
      top_k: topK,
    });
  }

  function run() {
    discover(portfolio);
  }

  return (
    <div className="content">
      <h1 className="page-title">Factors</h1>
      <p className="page-sub">
        Discover the macro risk factors that drive a portfolio or theme — each ranked by
        relevance and grounded in cited corpus evidence.
      </p>

      <div className="card col">
        <textarea
          className="textarea"
          placeholder="e.g. Long-duration US tech equity + IG credit, exposed to a Taiwan supply-chain shock"
          value={portfolio}
          onChange={(e) => setPortfolio(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
          }}
        />
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <div className="row">
            <button className="btn primary" onClick={run} disabled={factors.isPending}>
              {factors.isPending ? "Discovering…" : `Discover  ${submitHint}`}
            </button>
            <button className="btn" onClick={() => setShowFilters((v) => !v)}>
              Filters &amp; options{nFilters ? ` (${nFilters})` : ""}
            </button>
          </div>
        </div>

        {showFilters && (
          <div
            className="col"
            style={{ borderTop: "1px solid var(--border)", paddingTop: 12, gap: 10 }}
          >
            <FacetFilterEditor value={filters} onChange={setFilters} />
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <label className="row faint" style={{ fontSize: 13, gap: 6 }}>
                Factors to return
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={50}
                  value={topK}
                  onChange={(e) =>
                    setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))
                  }
                  style={{ width: 64 }}
                />
              </label>
              <div className="row" style={{ gap: 8 }}>
                <SavedViews current={filters} onApply={setFilters} />
                <button className="btn" onClick={() => setFilters(emptyFilter())}>
                  Clear filters
                </button>
              </div>
            </div>
          </div>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <RecentQueries
          items={history.items}
          onPick={(p) => {
            setPortfolio(p);
            discover(p);
          }}
          onClear={history.clear}
          label="Recent portfolios"
        />
      </div>

      {factors.isError && (
        <div className="callout bad" style={{ marginTop: 16 }}>
          {(factors.error as Error).message}
        </div>
      )}

      {result && (
        <div className="col" style={{ marginTop: 16 }}>
          <FactorsView result={result} query={portfolio.trim()} />
        </div>
      )}
    </div>
  );
}

const RELEVANCE_ORDER: Record<FactorRelevance, number> = { high: 0, medium: 1, low: 2 };
const RELEVANCE_TONE: Record<FactorRelevance, string> = {
  high: "badge good",
  medium: "badge",
  low: "badge warn",
};

function FactorsView({ result, query }: { result: FactorsResultDto; query?: string }) {
  useCitationPrefetch(result.factors.flatMap((f) => f.chunk_ids));
  const sorted = [...result.factors].sort(
    (a, b) =>
      (RELEVANCE_ORDER[a.relevance] ?? 9) - (RELEVANCE_ORDER[b.relevance] ?? 9),
  );
  const cites = citationIndex(sorted.map((f) => f.chunk_ids));

  if (sorted.length === 0) {
    return (
      <div className="callout warn">
        No macro factors were grounded for this portfolio — try a broader description or
        loosen filters.
      </div>
    );
  }

  return (
    <div className="col">
      <div className="card col">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <RouteBadge route={result.route} query={query} />
          <span className="badge">{sorted.length} factors</span>
          {result.world_view_id && (
            <span
              className="badge"
              title="corpus snapshot these factors were drawn from"
            >
              world view: {result.world_view_id}
            </span>
          )}
        </div>
        <div className="col" style={{ gap: 10 }}>
          {sorted.map((f) => (
            <FactorRow key={f.factor_tag} factor={f} cites={cites} />
          ))}
        </div>
      </div>

      <EvidenceRail chunks={result.chunks} cites={cites} />
    </div>
  );
}

function FactorRow({
  factor,
  cites,
}: {
  factor: RelevantFactor;
  cites: Map<string, number>;
}) {
  return (
    <div className="col row-highlight" style={{ gap: 4 }}>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <code style={{ fontWeight: 700 }}>{factor.factor_tag}</code>
        <span className={RELEVANCE_TONE[factor.relevance] ?? "badge"}>
          {factor.relevance}
        </span>
        {factor.entity_tags.map((t) => (
          <span key={t} className="badge">
            {t}
          </span>
        ))}
      </div>
      {factor.rationale && (
        <span className="muted" style={{ fontSize: 13 }}>
          {factor.rationale}
        </span>
      )}
      {factor.chunk_ids.length > 0 && (
        <span className="tag-list">
          {factor.chunk_ids.map((c) => (
            <CitationChip key={c} chunkId={c} index={cites.get(c) ?? 0} />
          ))}
        </span>
      )}
    </div>
  );
}
