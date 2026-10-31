import { useState } from "react";
import { useCitationPrefetch, useQueryAnswer, useSynthesisSchema } from "@/api/hooks";
import {
  emptyFilter,
  type FilterModel,
  type QueryResultDto,
  type ShockDirection,
  type ShockSchemaField,
  type SynthesisOptions,
} from "@/api/types";
import {
  FacetFilterEditor,
  activeFilterCount,
} from "@/components/FacetFilterEditor";
import { RecentQueries } from "@/components/RecentQueries";
import { SavedViews } from "@/components/SavedViews";
import { SHOCK_FIELDS, shockSpec } from "@/lib/shockVocab";
import { useHistory, useLocalState } from "@/lib/persist";
import { submitHint } from "@/lib/platform";
import { ShockVectorTable } from "./ShockVectorTable";
import { SynthesisDiagnosticsView } from "./SynthesisDiagnosticsView";
import { ScenarioCompare, type Pinned } from "./ScenarioCompare";
import { copyJson, downloadJson } from "@/lib/download";

// Phase P1 — builds a cited, gated shock vector via POST /query (mode=synthesize).
export function ScenarioPage() {
  const [prompt, setPrompt] = useState("");
  const [filters, setFilters] = useState<FilterModel>(emptyFilter());
  const [showFilters, setShowFilters] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [horizon, setHorizon] = useState(""); // horizon_days, blank = let synth infer
  const [fields, setFields] = useState<string[]>([]); // restrict emitted shock fields
  // expected sign per field; a field synthesized against it is flagged direction_mismatch
  const [directions, setDirections] = useState<Record<string, ShockDirection>>({});
  const [narrative, setNarrative] = useState(""); // synthesis.scenario
  // the options the shown result was requested with (the form may change after)
  const [sent, setSent] = useState<SynthesisOptions | null>(null);
  const [copied, setCopied] = useState(false);
  const [pins, setPins] = useLocalState<Pinned[]>("scenarioPins", []);
  const synth = useQueryAnswer();
  const schema = useSynthesisSchema();
  const history = useHistory("scenarioHistory");

  // Field names to offer in the restriction picker — prefer the live catalog,
  // fall back to the static contract vocab when the schema hasn't loaded.
  const fieldNames = schema.data?.fields.map((f) => f.name)
    ?? SHOCK_FIELDS.map((s) => s.field);

  function pin(result: QueryResultDto) {
    setPins((p) =>
      [...p, { label: `#${p.length + 1}`, prompt: prompt.trim(),
               vector: result.shock_vector ?? {} }].slice(-2));
  }

  const signOf = (name: string) =>
    schema.data?.fields.find((f) => f.name === name)?.positive_means
    ?? shockSpec(name)?.positiveMeans;
  const nDirections = Object.keys(directions).length;
  const optionTags = [
    horizon.trim() && "horizon",
    fields.length && `${fields.length} fields`,
    nDirections && `${nDirections} directions`,
    narrative.trim() && "narrative",
  ].filter(Boolean);

  function cycleDirection(name: string) {
    setDirections((prev) => {
      const next = { ...prev };
      if (!prev[name]) next[name] = "up";
      else if (prev[name] === "up") next[name] = "down";
      else delete next[name];
      return next;
    });
  }

  const nFilters = activeFilterCount(filters);
  const result = synth.data;

  function buildSynthesis(): SynthesisOptions | null {
    const h = Number(horizon);
    const opts: SynthesisOptions = {};
    if (horizon.trim() && Number.isFinite(h) && h > 0) opts.horizon_days = h;
    if (fields.length) opts.fields = fields;
    if (nDirections) opts.directions = directions;
    if (narrative.trim()) opts.scenario = narrative.trim();
    return Object.keys(opts).length ? opts : null;
  }

  function synthesize(q: string) {
    const t = q.trim();
    if (!t || synth.isPending) return;
    setCopied(false);
    history.add(t);
    const synthesis = buildSynthesis();
    setSent(synthesis);
    synth.mutate({
      question: t,
      filters: nFilters ? filters : null,
      mode: "synthesize",
      synthesis,
    });
  }

  function run() {
    synthesize(prompt);
  }

  return (
    <div className="content">
      <h1 className="page-title">Scenario Studio</h1>
      <p className="page-sub">
        Turn a macro narrative into a structured, cited shock vector for the AITrade
        stress engine. Every field must cite at least one dated source chunk to pass the
        gate.
      </p>

      <div className="card col">
        <textarea
          className="textarea"
          placeholder="e.g. Taiwan invasion tail risk over a 6-month horizon — rates, credit, vol and FX shocks"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) run();
          }}
        />
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button className="btn primary" onClick={run} disabled={synth.isPending}>
            {synth.isPending ? "Synthesizing…" : `Synthesize  ${submitHint}`}
          </button>
          <button className="btn" onClick={() => setShowFilters((v) => !v)}>
            Filters{nFilters ? ` (${nFilters})` : ""}
          </button>
          <button className="btn" onClick={() => setShowAdvanced((v) => !v)}>
            Options{optionTags.length ? ` (${optionTags.join(", ")})` : ""}
          </button>
        </div>

        {showFilters && (
          <div
            className="col"
            style={{ borderTop: "1px solid var(--border)", paddingTop: 12, gap: 10 }}
          >
            <FacetFilterEditor value={filters} onChange={setFilters} />
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
              <SavedViews current={filters} onApply={setFilters} />
              <button className="btn" onClick={() => setFilters(emptyFilter())}>
                Clear filters
              </button>
            </div>
          </div>
        )}

        {showAdvanced && (
          <div
            className="col"
            style={{ borderTop: "1px solid var(--border)", paddingTop: 12, gap: 12 }}
          >
            <label className="row" style={{ gap: 8, fontSize: 13 }}>
              <span className="faint">Horizon (days)</span>
              <input
                className="input"
                type="number"
                min={1}
                placeholder="infer from prompt"
                value={horizon}
                onChange={(e) => setHorizon(e.target.value)}
                style={{ width: 160 }}
              />
              <span className="faint" style={{ fontSize: 12 }}>
                pins the shock horizon structurally (moves are cumulative over it)
              </span>
            </label>

            <div className="col" style={{ gap: 6 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="faint" style={{ fontSize: 13 }}>
                  Restrict to fields{" "}
                  {fields.length ? `(${fields.length} selected)` : "(all)"}
                </span>
                {fields.length > 0 && (
                  <button className="btn" onClick={() => setFields([])}>
                    Clear
                  </button>
                )}
              </div>
              <div className="tag-list">
                {fieldNames.map((name) => {
                  const on = fields.includes(name);
                  return (
                    <button
                      key={name}
                      className="chip"
                      style={{
                        borderColor: on ? "var(--accent)" : undefined,
                        color: on ? "var(--accent)" : undefined,
                      }}
                      onClick={() =>
                        setFields((prev) =>
                          on ? prev.filter((f) => f !== name) : [...prev, name],
                        )
                      }
                    >
                      {name}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="col" style={{ gap: 6 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="faint" style={{ fontSize: 13 }}>
                  Expected direction{" "}
                  {nDirections ? `(${nDirections} set)` : "(none — sign not checked)"}
                </span>
                {nDirections > 0 && (
                  <button className="btn" onClick={() => setDirections({})}>
                    Clear
                  </button>
                )}
              </div>
              <span className="faint" style={{ fontSize: 12 }}>
                click to cycle ↑ / ↓ / unset. ↑ means a positive value under the field's
                sign convention (hover a chip); a field synthesized with the other sign
                is flagged <code>direction_mismatch</code> and dropped. Set directions
                whenever the scenario has them.
              </span>
              <div className="tag-list">
                {fieldNames.map((name) => {
                  const d = directions[name];
                  return (
                    <button
                      key={name}
                      className="chip"
                      title={`+ = ${signOf(name) ?? "?"}`}
                      style={{
                        borderColor: d ? "var(--accent)" : undefined,
                        color: d ? "var(--accent)" : undefined,
                      }}
                      onClick={() => cycleDirection(name)}
                    >
                      {name}
                      {d === "up" ? " ↑" : d === "down" ? " ↓" : ""}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="col" style={{ gap: 6 }}>
              <span className="faint" style={{ fontSize: 13 }}>
                Scenario narrative (optional)
              </span>
              <textarea
                className="textarea"
                style={{ minHeight: 64 }}
                placeholder="extra scenario detail folded into retrieval + synthesis, so the prompt above can stay short"
                value={narrative}
                onChange={(e) => setNarrative(e.target.value)}
              />
            </label>
          </div>
        )}
      </div>

      <div style={{ marginTop: 12 }}>
        <RecentQueries
          items={history.items}
          onPick={(q) => {
            setPrompt(q);
            synthesize(q);
          }}
          onClear={history.clear}
          label="Recent scenarios"
        />
      </div>

      {synth.isError && (
        <div className="callout bad" style={{ marginTop: 16 }}>
          {(synth.error as Error).message}
        </div>
      )}

      {result && (
        <div className="col" style={{ marginTop: 16 }}>
          <ScenarioResult
            result={result}
            schema={schema.data?.fields}
            requested={sent?.fields}
            copied={copied}
            onCopy={async () => setCopied(await copyJson(exportPayload(result, prompt)))}
            onDownload={() =>
              downloadJson("shock_vector.json", exportPayload(result, prompt))
            }
            onPin={() => pin(result)}
          />
        </div>
      )}

      {pins.length === 2 && (
        <div className="col" style={{ marginTop: 16 }}>
          <ScenarioCompare a={pins[0]} b={pins[1]} onClear={() => setPins([])} />
        </div>
      )}
      {pins.length === 1 && (
        <p className="faint" style={{ marginTop: 12 }}>
          Pinned 1 scenario — synthesize and pin another to compare.
        </p>
      )}
    </div>
  );
}

function ScenarioResult({
  result,
  schema,
  requested,
  copied,
  onCopy,
  onDownload,
  onPin,
}: {
  result: QueryResultDto;
  schema?: ShockSchemaField[];
  requested?: string[];
  copied: boolean;
  onCopy: () => void;
  onDownload: () => void;
  onPin: () => void;
}) {
  const vector = result.shock_vector ?? {};
  const nFields = Object.keys(vector).length;
  const diag = result.synthesis_diagnostics;
  useCitationPrefetch(Object.values(vector).flatMap((f) => f.chunk_ids));

  if (result.refused) {
    return (
      <div className="col">
        <div className="callout warn">
          Synthesis refused — evidence fell below the relevance floor. Try a broader
          scenario or loosen filters.
        </div>
        {diag && <SynthesisDiagnosticsView diag={diag} />}
      </div>
    );
  }

  return (
    <>
      <div className="card col">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <span className="badge">{nFields} grounded fields</span>
          {result.world_view_id && (
            <span className="badge" title="corpus-state id this scenario was synthesized against — a provenance/freshness stamp, not a queryable snapshot">
              world view: {result.world_view_id}
            </span>
          )}
          <div className="spacer" style={{ flex: 1 }} />
          <button className="btn" onClick={onCopy} disabled={nFields === 0}>
            {copied ? "Copied ✓" : "Copy JSON"}
          </button>
          <button className="btn" onClick={onDownload} disabled={nFields === 0}>
            Download
          </button>
          <button className="btn" onClick={onPin} disabled={nFields === 0}>
            Pin to compare
          </button>
        </div>
        {result.summary && <p style={{ margin: 0 }}>{result.summary}</p>}
      </div>

      {diag && <SynthesisDiagnosticsView diag={diag} />}

      {nFields === 0 ? (
        <div className="callout warn">
          No shock fields passed the citation gate for this scenario.
        </div>
      ) : (
        <ShockVectorTable
          vector={vector}
          schema={schema}
          request={{ requested, missing: diag?.missing_fields }}
        />
      )}
    </>
  );
}

function exportPayload(result: QueryResultDto, prompt: string) {
  return {
    query: prompt.trim(),
    world_view_id: result.world_view_id ?? null,
    shock_vector: result.shock_vector ?? {},
  };
}
