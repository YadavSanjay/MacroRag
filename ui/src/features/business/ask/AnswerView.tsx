import { useState } from "react";
import { useCitationPrefetch } from "@/api/hooks";
import type { QueryResultDto, TimeseriesHit } from "@/api/types";
import { CitationChip } from "@/components/Citations";
import { ConfidenceReadout } from "@/components/ConfidenceReadout";
import { EvidenceRail } from "@/components/EvidenceRail";
import { RouteBadge } from "@/components/RouteBadge";
import { citationIndex } from "@/lib/citations";
import { copyText, downloadJson, downloadText } from "@/lib/download";

export function AnswerView({
  result,
  query,
}: {
  result: QueryResultDto;
  query?: string;
}) {
  // Warm the citation cache for every cited chunk in one batch request.
  useCitationPrefetch(result.used_chunk_ids);

  // Number cited sources by reading order, then any remaining cited ids.
  const cites = citationIndex([
    ...result.details.map((d) => d.chunk_ids),
    result.used_chunk_ids,
  ]);

  if (result.refused) {
    return (
      <div className="card">
        <div className="row" style={{ marginBottom: 8 }}>
          <RouteBadge route={result.route} query={query} />
          <span className="badge warn">refused</span>
        </div>
        <div className="callout warn">
          No confident answer — the retrieved evidence wasn't relevant enough to
          this question, and no matching data series was found. Try broadening the
          question, rephrasing it, or loosening filters.
        </div>
      </div>
    );
  }

  return (
    <div className="col">
      <div className="card col">
        <div className="row" style={{ flexWrap: "wrap", alignItems: "center" }}>
          <RouteBadge route={result.route} query={query} />
          {result.cached && <span className="badge">cached</span>}
          {result.world_view_id && (
            <span className="badge">{result.world_view_id}</span>
          )}
          <div className="spacer" style={{ flex: 1 }} />
          <AnswerExport result={result} query={query} />
        </div>

        <ConfidenceReadout result={result} />

        <p className="answer-prose" style={{ margin: 0 }}>{result.summary}</p>

        {result.details.length > 0 && (
          <div className="col answer-prose" style={{ gap: 10 }}>
            {result.details.map((d, i) => (
              <div key={i}>
                <span>{d.text} </span>
                <span className="tag-list" style={{ display: "inline-flex" }}>
                  {d.chunk_ids.map((c) => (
                    <CitationChip key={c} chunkId={c} index={cites.get(c) ?? 0} />
                  ))}
                </span>
              </div>
            ))}
          </div>
        )}

        {result.unanswered.length > 0 && (
          <div className="callout warn">
            <strong>Not covered by the corpus:</strong>
            <ul style={{ margin: "6px 0 0" }}>
              {result.unanswered.map((u, i) => (
                <li key={i}>{u}</li>
              ))}
            </ul>
          </div>
        )}

        {result.unsupported.length > 0 && (
          <div className="callout bad">
            <strong>Dropped by faithfulness verifier:</strong>
            <ul style={{ margin: "6px 0 0" }}>
              {result.unsupported.map((u, i) => (
                <li key={i}>{u}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {result.timeseries.length > 0 && (
        <div className="card col">
          <strong>Series evidence</strong>
          <div className="tag-list">
            {result.timeseries.map((t, i) => (
              <TimeseriesCard key={i} hit={t} />
            ))}
          </div>
        </div>
      )}

      <EvidenceRail chunks={result.chunks} cites={cites} />
    </div>
  );
}

function AnswerExport({ result, query }: { result: QueryResultDto; query?: string }) {
  const [copied, setCopied] = useState(false);
  const payload = answerPayload(result, query);
  return (
    <div className="row" style={{ gap: 6 }}>
      <button
        className="btn"
        onClick={async () => setCopied(await copyText(answerMarkdown(result, query)))}
        title="copy the answer as Markdown"
      >
        {copied ? "Copied ✓" : "Copy MD"}
      </button>
      <button
        className="btn"
        onClick={() => downloadText("answer.md", answerMarkdown(result, query))}
      >
        .md
      </button>
      <button className="btn" onClick={() => downloadJson("answer.json", payload)}>
        .json
      </button>
    </div>
  );
}

function answerPayload(result: QueryResultDto, query?: string) {
  return {
    question: query ?? null,
    route: result.route,
    world_view_id: result.world_view_id ?? null,
    summary: result.summary,
    details: result.details,
    unanswered: result.unanswered,
    unsupported: result.unsupported,
    cited_chunk_ids: result.used_chunk_ids,
  };
}

function answerMarkdown(result: QueryResultDto, query?: string): string {
  const lines: string[] = [];
  if (query) lines.push(`# ${query}`, "");
  lines.push(result.summary, "");
  for (const d of result.details) {
    const cites = d.chunk_ids.length ? ` [${d.chunk_ids.join(", ")}]` : "";
    lines.push(`- ${d.text}${cites}`);
  }
  if (result.unanswered.length) {
    lines.push("", "## Not covered by the corpus");
    result.unanswered.forEach((u) => lines.push(`- ${u}`));
  }
  if (result.unsupported.length) {
    lines.push("", "## Dropped by faithfulness verifier");
    result.unsupported.forEach((u) => lines.push(`- ${u}`));
  }
  if (result.world_view_id) lines.push("", `_world view: ${result.world_view_id}_`);
  return lines.join("\n");
}

function TimeseriesCard({ hit }: { hit: TimeseriesHit }) {
  return (
    <div className="card" style={{ padding: 12, minWidth: 200 }}>
      <div className="row">
        <span className="badge">{hit.source}</span>
        <span className="faint">{hit.n_rows} rows</span>
      </div>
      <div className="muted" style={{ fontSize: 12 }}>
        {hit.publisher}
      </div>
      {hit.note && (
        <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
          {hit.note}
        </div>
      )}
    </div>
  );
}
