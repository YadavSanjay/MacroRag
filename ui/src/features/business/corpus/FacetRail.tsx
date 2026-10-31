import { useState } from "react";
import type { CorpusFacets, CorpusFilters, FacetCount } from "@/api/corpus";

type ArrayKey = "tiers" | "doc_types" | "publishers" | "buckets" | "entity_tags" | "factor_tags";

// Facet sidebar: toggleable chips per facet (OR within a facet, AND across facets),
// a title search, and a release-date window. Mutating any facet resets paging.
export function FacetRail({
  facets,
  filters,
  onChange,
}: {
  facets: CorpusFacets;
  filters: CorpusFilters;
  onChange: (next: CorpusFilters) => void;
}) {
  const patch = (p: Partial<CorpusFilters>) => onChange({ ...filters, offset: 0, ...p });

  function toggle(key: ArrayKey, value: string | number) {
    const cur = filters[key] as (string | number)[];
    const next = cur.includes(value)
      ? cur.filter((v) => v !== value)
      : [...cur, value];
    patch({ [key]: next } as Partial<CorpusFilters>);
  }

  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="col" style={{ gap: 6 }}>
        <span className="faint" style={{ fontSize: 11 }}>SEARCH</span>
        <input
          className="input"
          placeholder="title / source…"
          value={filters.q}
          onChange={(e) => patch({ q: e.target.value })}
        />
      </div>

      <div className="row" style={{ gap: 8 }}>
        <label className="col" style={{ gap: 4, flex: 1 }}>
          <span className="faint" style={{ fontSize: 11 }}>FROM</span>
          <input className="input" type="date" value={filters.date_from ?? ""}
                 onChange={(e) => patch({ date_from: e.target.value || null })} />
        </label>
        <label className="col" style={{ gap: 4, flex: 1 }}>
          <span className="faint" style={{ fontSize: 11 }}>TO</span>
          <input className="input" type="date" value={filters.date_to ?? ""}
                 onChange={(e) => patch({ date_to: e.target.value || null })} />
        </label>
      </div>

      <FacetGroup label="Tier" items={facets.tiers}
                  selected={filters.tiers} onToggle={(v) => toggle("tiers", v)} />
      <FacetGroup label="Doc type" items={facets.doc_types}
                  selected={filters.doc_types} onToggle={(v) => toggle("doc_types", v)} />
      <FacetGroup label="Publisher" items={facets.publishers}
                  selected={filters.publishers} onToggle={(v) => toggle("publishers", v)} />
      <FacetGroup label="Entity tags" items={facets.entity_tags}
                  selected={filters.entity_tags} onToggle={(v) => toggle("entity_tags", v)} />
      <FacetGroup label="Factor tags" items={facets.factor_tags}
                  selected={filters.factor_tags} onToggle={(v) => toggle("factor_tags", v)} />
      <FacetGroup label="Bucket" items={facets.buckets}
                  selected={filters.buckets} onToggle={(v) => toggle("buckets", v)} />
    </div>
  );
}

function FacetGroup({
  label,
  items,
  selected,
  onToggle,
}: {
  label: string;
  items: FacetCount[];
  selected: (string | number)[];
  onToggle: (v: string | number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (items.length === 0) return null;
  const shown = expanded ? items : items.slice(0, 8);

  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>
        {label}
      </span>
      <div className="tag-list">
        {shown.map((it) => {
          const active = selected.includes(it.value);
          return (
            <button
              key={String(it.value)}
              className="chip"
              onClick={() => onToggle(it.value)}
              style={{
                borderColor: active ? "var(--accent)" : undefined,
                color: active ? "var(--accent)" : undefined,
                background: active ? "var(--bg-elev-2)" : undefined,
              }}
            >
              {it.value} <span className="faint">· {it.count}</span>
            </button>
          );
        })}
      </div>
      {items.length > 8 && (
        <button className="chip" onClick={() => setExpanded((v) => !v)} style={{ alignSelf: "flex-start" }}>
          {expanded ? "less" : `+${items.length - 8} more`}
        </button>
      )}
    </div>
  );
}
