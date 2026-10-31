import { useState } from "react";
import { useStreamingQuery } from "@/api/hooks";
import { emptyFilter, type FilterModel } from "@/api/types";
import {
  FacetFilterEditor,
  activeFilterCount,
} from "@/components/FacetFilterEditor";
import { RecentQueries } from "@/components/RecentQueries";
import { SavedViews } from "@/components/SavedViews";
import { useHistory } from "@/lib/persist";
import { submitHint } from "@/lib/platform";
import { AnswerView } from "./AnswerView";
import { FeedbackBar } from "./FeedbackBar";

export function AskPage() {
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState<FilterModel>(emptyFilter());
  const [showFilters, setShowFilters] = useState(false);
  const { state, run, cancel } = useStreamingQuery();
  const history = useHistory("askHistory");

  const nFilters = activeFilterCount(filters);

  function ask(q: string) {
    const t = q.trim();
    if (!t || state.running) return;
    history.add(t);
    run({ question: t, filters: nFilters ? filters : null, mode: "answer" });
  }

  function submit() {
    ask(question);
  }

  return (
    <div className="content">
      <h1 className="page-title">Ask</h1>
      <p className="page-sub">
        Cited answers over the macro corpus — hybrid retrieval → rerank → grounded
        generation, with faithfulness verification.
      </p>

      <div className="card col">
        <textarea
          className="textarea"
          placeholder="e.g. What is the severely adverse unemployment path in DFAST 2026?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
          }}
        />
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <div className="row">
            <button className="btn primary" onClick={submit} disabled={state.running}>
              {state.running ? "Running…" : `Ask  ${submitHint}`}
            </button>
            {state.running && (
              <button className="btn" onClick={cancel}>
                Cancel
              </button>
            )}
            <button className="btn" onClick={() => setShowFilters((v) => !v)}>
              Filters{nFilters ? ` (${nFilters})` : ""}
            </button>
          </div>
          {state.stage && <span className="badge">● {state.stage}</span>}
          {state.retrieved && (
            <span className="muted">
              retrieved {state.retrieved.n_chunks} · route {state.retrieved.route}
              {state.retrieved.cached ? " · cached" : ""}
            </span>
          )}
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
      </div>

      <div style={{ marginTop: 12 }}>
        <RecentQueries
          items={history.items}
          onPick={(q) => {
            setQuestion(q);
            ask(q);
          }}
          onClear={history.clear}
        />
      </div>

      {state.error && (
        <div className="callout bad" style={{ marginTop: 16 }}>
          {state.error}
        </div>
      )}

      {state.result && (
        <div className="col" style={{ marginTop: 16 }}>
          <AnswerView result={state.result} query={question.trim()} />
          {!state.result.refused && (
            <FeedbackBar
              question={question.trim()}
              chunkIds={state.result.used_chunk_ids}
            />
          )}
        </div>
      )}
    </div>
  );
}
