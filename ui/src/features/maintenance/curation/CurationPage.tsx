import { useState } from "react";
import {
  useTriggerCurate,
  useVocabulary,
  type CurateRequest,
} from "@/api/admin";
import { Async } from "@/components/Async";
import { JobConsole } from "@/components/JobConsole";

interface TaskDef {
  task: string;
  title: string;
  blurb: string;
  // "dry" = supports --dry-run (default report-only); "strip" = dfast strip-only toggle
  mode: "dry" | "strip";
}

const TASKS: TaskDef[] = [
  {
    task: "normalize-vocab",
    title: "Normalize vocabulary",
    blurb: "Canonicalize factor/entity tags against the alias map and reclassify unknown doc types.",
    mode: "dry",
  },
  {
    task: "rechunk",
    title: "Re-chunk documents",
    blurb: "Re-split oversized chunks with the current budget splitter (numeric docs skipped).",
    mode: "dry",
  },
  {
    task: "reextract-sep",
    title: "Re-extract SEP projections",
    blurb: "Rebuild SEP numeric series from the Fed HTML projections page; narrative untouched.",
    mode: "dry",
  },
  {
    task: "reextract-dfast",
    title: "Re-extract DFAST scenarios",
    blurb: "Ingest DFAST scenario CSVs and retire the legacy PDF grid. Strip-only skips the network ingest.",
    mode: "strip",
  },
];

// P4 — Curation: run the maintenance scripts as tracked jobs (dry-run by default)
// and inspect the controlled vocabulary.
export function CurationPage() {
  const [activeJob, setActiveJob] = useState<string | null>(null);

  return (
    <div className="content">
      <h1 className="page-title">Curation</h1>
      <p className="page-sub">
        Corpus maintenance — vocabulary canonicalization, re-chunking, and numeric
        re-extraction. Tasks run as jobs; most default to a dry run (report only).
      </p>

      <div className="col">
        {TASKS.map((t) => (
          <TaskRow key={t.task} def={t} onStarted={setActiveJob} />
        ))}
      </div>

      {activeJob && (
        <div className="col" style={{ marginTop: 16 }}>
          <strong>Current task</strong>
          <JobConsole jobId={activeJob} />
        </div>
      )}

      <VocabularySection />
    </div>
  );
}

function TaskRow({ def, onStarted }: { def: TaskDef; onStarted: (id: string) => void }) {
  const trigger = useTriggerCurate();
  const [dryRun, setDryRun] = useState(true);
  const [stripOnly, setStripOnly] = useState(true);

  function run() {
    const req: CurateRequest =
      def.mode === "dry"
        ? { task: def.task, dry_run: dryRun }
        : { task: def.task, strip_only: stripOnly };
    trigger.mutate(req, { onSuccess: (j) => onStarted(j.id) });
  }

  const applying = def.mode === "dry" ? !dryRun : !stripOnly;

  return (
    <div className="card row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 16 }}>
      <div className="col" style={{ gap: 4, flex: 1 }}>
        <strong>{def.title}</strong>
        <span className="muted" style={{ fontSize: 13 }}>{def.blurb}</span>
        {trigger.isError && (
          <span className="badge bad">{(trigger.error as Error).message}</span>
        )}
      </div>
      <div className="col" style={{ gap: 8, alignItems: "flex-end" }}>
        {def.mode === "dry" ? (
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
            <span className="faint">dry run</span>
          </label>
        ) : (
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={stripOnly} onChange={(e) => setStripOnly(e.target.checked)} />
            <span className="faint">strip only (no network)</span>
          </label>
        )}
        <button
          className={`btn ${applying ? "primary" : ""}`}
          disabled={trigger.isPending}
          onClick={run}
          title={applying ? "writes changes" : "report only"}
        >
          {applying ? "Run (apply)" : "Run (report)"}
        </button>
      </div>
    </div>
  );
}

function VocabularySection() {
  const q = useVocabulary();
  const [open, setOpen] = useState(false);
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Controlled vocabulary</strong>
        <button className="chip" onClick={() => setOpen((v) => !v)}>{open ? "hide" : "show"}</button>
      </div>
      <Async q={q}>
        {q.data && (
          <>
            <span className="faint">
              {q.data.factor_tags.length} factor tags · {q.data.entity_tags.length} entity
              tags · {q.data.doc_types.length} doc types · {Object.keys(q.data.aliases).length} aliases
            </span>
            {open && (
              <div className="col" style={{ gap: 12 }}>
                <TagGroup label="Factor tags" tags={q.data.factor_tags} />
                <TagGroup label="Entity tags" tags={q.data.entity_tags} />
                <TagGroup label="Doc types" tags={q.data.doc_types} />
                <div className="col" style={{ gap: 6 }}>
                  <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>
                    Aliases
                  </span>
                  <div className="tag-list">
                    {Object.entries(q.data.aliases).map(([from, to]) => (
                      <span key={from} className="chip" style={{ cursor: "default" }}>
                        {from} → {to}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </>
        )}
      </Async>
    </div>
  );
}

function TagGroup({ label, tags }: { label: string; tags: string[] }) {
  if (tags.length === 0) return null;
  return (
    <div className="col" style={{ gap: 6 }}>
      <span className="faint" style={{ fontSize: 11, textTransform: "uppercase" }}>{label}</span>
      <div className="tag-list">
        {tags.map((t) => (
          <span key={t} className="chip" style={{ cursor: "default" }}>{t}</span>
        ))}
      </div>
    </div>
  );
}
