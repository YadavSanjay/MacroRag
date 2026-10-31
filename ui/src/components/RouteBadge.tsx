import { useNavigate } from "@tanstack/react-router";
import type { RetrievalRoute } from "@/api/types";

const LABELS: Record<RetrievalRoute, string> = {
  DIRECT: "direct retrieval",
  PARAPHRASE: "paraphrase expansion",
  DECOMPOSE: "decomposed sub-queries",
  HYDE: "hypothetical-doc embedding",
  PLAN_EXECUTE: "quantitative plan-execute",
};

// The retrieval route is the bridge into the technical Trace Explorer. When a
// `query` is supplied the badge becomes a deep-link that opens Traces pre-filtered
// to that query (the just-run request is the most recent trace).
export function RouteBadge({
  route,
  query,
}: {
  route: RetrievalRoute;
  query?: string;
}) {
  const navigate = useNavigate();
  const label = LABELS[route] ?? route.toLowerCase();

  if (!query) {
    return (
      <span className="badge" title={`retrieval route: ${route}`}>
        {label}
      </span>
    );
  }
  return (
    <span
      className="badge"
      role="button"
      tabIndex={0}
      title={`retrieval route: ${route} — open in Traces`}
      style={{ cursor: "pointer" }}
      onClick={() =>
        navigate({ to: "/system/traces", search: { q: query } } as never)
      }
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          navigate({ to: "/system/traces", search: { q: query } } as never);
        }
      }}
    >
      {label} ↗
    </span>
  );
}
