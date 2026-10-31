import type { FilterModel } from "@/api/types";

// Compact editor for the shared FilterModel (the controlled-vocabulary retrieval
// filters). Reused by Ask, Scenario Studio, Corpus and Eval. List facets are entered
// as comma-separated tokens; unknown tokens are preserved (backend canonicalizes via
// the vocabulary alias map).
export function FacetFilterEditor({
  value,
  onChange,
}: {
  value: FilterModel;
  onChange: (next: FilterModel) => void;
}) {
  const set = (patch: Partial<FilterModel>) => onChange({ ...value, ...patch });

  return (
    <div className="col">
      <Field label="Tiers (e.g. 1,2,3)">
        <input
          className="input"
          value={value.tiers.join(",")}
          placeholder="1,2,3"
          onChange={(e) => set({ tiers: parseInts(e.target.value) })}
        />
      </Field>

      <ListField
        label="Factor tags"
        placeholder="discount_rate, term_premium"
        value={value.factor_tags}
        onChange={(v) => set({ factor_tags: v })}
      />
      <ListField
        label="Entity tags"
        placeholder="china, ai_capex"
        value={value.entity_tags}
        onChange={(v) => set({ entity_tags: v })}
      />
      <ListField
        label="Doc types"
        placeholder="fomc_statement, sep_projections"
        value={value.doc_types}
        onChange={(v) => set({ doc_types: v })}
      />
      <ListField
        label="Publishers"
        placeholder="Federal Reserve, US Treasury"
        value={value.publishers}
        onChange={(v) => set({ publishers: v })}
      />
      <ListField
        label="Buckets (A/B/C)"
        placeholder="A, B"
        value={value.buckets}
        onChange={(v) => set({ buckets: v })}
      />

      <div className="row">
        <Field label="From">
          <input
            className="input"
            type="date"
            value={value.date_from ?? ""}
            onChange={(e) => set({ date_from: e.target.value || null })}
          />
        </Field>
        <Field label="To">
          <input
            className="input"
            type="date"
            value={value.date_to ?? ""}
            onChange={(e) => set({ date_to: e.target.value || null })}
          />
        </Field>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="col" style={{ gap: 4, flex: 1 }}>
      <span className="faint" style={{ fontSize: 11 }}>
        {label}
      </span>
      {children}
    </label>
  );
}

function ListField({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  return (
    <Field label={label}>
      <input
        className="input"
        value={value.join(", ")}
        placeholder={placeholder}
        onChange={(e) => onChange(parseList(e.target.value))}
      />
    </Field>
  );
}

function parseList(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

function parseInts(s: string): number[] {
  return parseList(s)
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
}

export function activeFilterCount(f: FilterModel): number {
  return (
    f.tiers.length +
    f.buckets.length +
    f.doc_types.length +
    f.entity_tags.length +
    f.factor_tags.length +
    f.publishers.length +
    (f.date_from ? 1 : 0) +
    (f.date_to ? 1 : 0)
  );
}
