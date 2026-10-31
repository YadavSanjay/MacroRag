import { useState } from "react";
import { useAdminConfig, type AdminConfigDto } from "@/api/admin";
import { Async } from "@/components/Async";
import { Drawer } from "@/components/Drawer";

// P2 — System Map: an annotated component graph from ingest to API. Static topology
// (the package layout) overlaid with live config + active implementations from
// GET /admin/config, so engineers see what is actually wired and configured.

interface Node {
  id: string;
  title: string;
  files: string;
  desc: string;
  impl?: string | null;
  meta?: string[];
}

interface Stage {
  label: string;
  nodes: Node[];
}

function buildStages(cfg: AdminConfigDto): Stage[] {
  const impl = cfg.active_impls;
  return [
    {
      label: "Ingestion & Indexing",
      nodes: [
        {
          id: "ingest",
          title: "Ingest",
          files: "src/ingest/*",
          desc: "Tiered source handlers fetch, parse and persist ParsedDocument JSON (narrative chunks + numeric timeseries).",
        },
        {
          id: "index",
          title: "Index pipeline",
          files: "src/pipelines/index_pipeline.py",
          desc: "Embeds chunks and upserts into the vector store; loads numeric rows into the time-series store; writes the manifest.",
          meta: [`embedding ${cfg.embedding.model}`, `${cfg.embedding.dense_dim}d`],
        },
      ],
    },
    {
      label: "Stores",
      nodes: [
        {
          id: "vector",
          title: "Vector store",
          files: "src/vectorstores/*",
          desc: "Hybrid dense+sparse store with RRF fusion and facet filtering.",
          impl: impl?.vector_store,
          meta: [`collection ${cfg.vector_store.collection}`, `mode ${cfg.vector_store.mode}`],
        },
        {
          id: "timeseries",
          title: "Time-series store",
          files: "src/vectorstores/timeseries_store.py",
          desc: "SQLite store of exact numeric observations, hydrated into quantitative answers.",
          impl: impl?.timeseries_store,
        },
        {
          id: "embedder",
          title: "Embedder",
          files: "src/embeddings/*",
          desc: "bge-m3 dense+sparse embeddings (hashing embedder under test).",
          impl: impl?.embedder,
        },
      ],
    },
    {
      label: "Retrieval",
      nodes: [
        {
          id: "analysis",
          title: "Query analysis",
          files: "src/retriever/analysis.py",
          desc: "Routing + facet extraction; optional paraphrase / decompose / HyDE expansion.",
          impl: impl?.analyzer,
        },
        {
          id: "fusion",
          title: "Fusion + rerank",
          files: "src/retriever/{fusion,rerank,ranking,mmr}.py",
          desc: "RRF across branches → cross-encoder rerank → time-decay → MMR diversification → neighbour expansion.",
          impl: impl?.reranker,
          meta: [
            `rerank ${cfg.reranker.enabled ? "on" : "off"}`,
            `mmr λ ${cfg.retrieval.mmr_lambda}`,
            `final_k ${cfg.retrieval.final_k}`,
            `time-decay ${cfg.ranking.time_decay ? "on" : "off"}`,
          ],
        },
        {
          id: "agent",
          title: "Plan-execute agent",
          files: "src/agent/*",
          desc: "Typed plan + safe calculator for quantitative queries (data lookup + compute + sub-retrieval).",
          impl: impl?.agent,
          meta: [`plan_execute ${cfg.retrieval.plan_execute ? "on" : "off"}`],
        },
      ],
    },
    {
      label: "Generation",
      nodes: [
        {
          id: "synthesis",
          title: "Synthesis + gate",
          files: "src/synthesis/*",
          desc: "Shock-vector extraction with the controlled vocabulary and citation gate (the AITrade contract).",
        },
        {
          id: "llm",
          title: "LLM generate + verify",
          files: "src/llm/{generate,verify}.py",
          desc: "Tiered cited generation; faithfulness verifier drops unsupported claims.",
          impl: impl?.llm,
          meta: [
            `answer ${cfg.llm.answer_model}`,
            `complex ${cfg.llm.complex_model}`,
            `verify ${cfg.retrieval.verify_faithfulness ? "on" : "off"}`,
            `floor ${cfg.retrieval.min_rerank_score}`,
          ],
        },
      ],
    },
    {
      label: "Serving",
      nodes: [
        {
          id: "api",
          title: "API",
          files: "src/api/*",
          desc: "FastAPI: query (+synthesize) / stream / citations / feedback / healthz / admin. API-key auth + principal injection.",
          meta: [`auth ${cfg.auth.enabled ? "on" : "off"}`, `otel ${cfg.observability.otel_enabled ? "on" : "off"}`],
        },
      ],
    },
  ];
}

export function SystemMapPage() {
  const cfg = useAdminConfig();
  const [node, setNode] = useState<Node | null>(null);

  return (
    <div className="content">
      <h1 className="page-title">System Map</h1>
      <p className="page-sub">
        Component topology overlaid with live configuration and the active interface
        implementations. Click any component for detail.
      </p>

      <Async q={cfg}>
        {cfg.data && (
          <>
            {!cfg.data.built && (
              <div className="callout warn" style={{ marginBottom: 16 }}>
                The pipeline isn't built yet — run a query so the concrete
                implementations (embedder, store, reranker, LLM) are reported.
              </div>
            )}
            <div className="col" style={{ gap: 0 }}>
              {buildStages(cfg.data).map((stage, i, arr) => (
                <div key={stage.label}>
                  <div className="faint" style={{ fontSize: 11, textTransform: "uppercase", margin: "4px 0" }}>
                    {stage.label}
                  </div>
                  <div className="row" style={{ flexWrap: "wrap", alignItems: "stretch" }}>
                    {stage.nodes.map((n) => (
                      <NodeCard key={n.id} node={n} onClick={() => setNode(n)} />
                    ))}
                  </div>
                  {i < arr.length - 1 && (
                    <div style={{ textAlign: "center", color: "var(--text-faint)", margin: "6px 0" }}>
                      ↓
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </Async>

      {node && <NodeDrawer node={node} onClose={() => setNode(null)} />}
    </div>
  );
}

function NodeCard({ node, onClick }: { node: Node; onClick: () => void }) {
  return (
    <button
      className="card"
      onClick={onClick}
      style={{
        flex: "1 1 220px",
        minWidth: 200,
        textAlign: "left",
        cursor: "pointer",
        background: "var(--bg-elev)",
      }}
    >
      <div style={{ fontWeight: 700 }}>{node.title}</div>
      {node.impl && (
        <div style={{ marginTop: 4 }}>
          <span className="badge good">{node.impl}</span>
        </div>
      )}
      {node.meta && node.meta.length > 0 && (
        <div className="tag-list" style={{ marginTop: 6 }}>
          {node.meta.map((m) => (
            <span key={m} className="chip" style={{ cursor: "pointer" }}>{m}</span>
          ))}
        </div>
      )}
    </button>
  );
}

function NodeDrawer({ node, onClose }: { node: Node; onClose: () => void }) {
  return (
    <Drawer title={node.title} onClose={onClose}>
      <div className="col">
        {node.impl && <span className="badge good">active: {node.impl}</span>}
        <p>{node.desc}</p>
        <div className="row" style={{ gap: 8 }}>
          <span className="faint" style={{ width: 60 }}>files</span>
          <code>{node.files}</code>
        </div>
        {node.meta && node.meta.length > 0 && (
          <div className="tag-list">
            {node.meta.map((m) => (
              <span key={m} className="chip" style={{ cursor: "default" }}>{m}</span>
            ))}
          </div>
        )}
      </div>
    </Drawer>
  );
}
