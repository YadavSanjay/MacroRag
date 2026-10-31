import { useState } from "react";
import {
  BACKTEST_MODE_LABEL,
  BACKTEST_MODES,
  isTerminal,
  SELFCHECK_LOWER_BETTER,
  useBacktestEpisodes,
  useEvalBaseline,
  useEvalHistory,
  useFeedback,
  useGolden,
  useJob,
  useReconcileFeedback,
  useSchemaHealth,
  useSetBaseline,
  useTriggerBacktest,
  useTriggerEval,
  useTriggerSelfCheck,
  type BacktestMode,
  type BacktestResult,
  type BacktestRow,
  type EvalHistoryRun,
  type EvalResult,
  type EvalRow,
  type FeedbackItem,
  type GoldenQuestion,
  type JudgeRow,
  type SelfCheckNumericRow,
  type SelfCheckProbeRow,
  type SelfCheckResult,
} from "@/api/admin";
import { Async, StatCard } from "@/components/Async";
import { JobConsole } from "@/components/JobConsole";

// P4 — Quality: golden-set eval (as a job, optionally LLM-judged), drift gate vs
// baseline, structured-output health, and the feedback inbox → golden candidates.
export function QualityPage() {
  return (
    <div className="content">
      <h1 className="page-title">Quality</h1>
      <p className="page-sub">
        Retrieval evaluation against the golden set, drift versus the saved baseline,
        structured-output health, and the user-feedback inbox.
      </p>
      <EvalSection />
      <SelfCheckSection />
      <BacktestSection />
      <BaselineSection />
      <HistorySection />
      <SchemaHealthSection />
      <GoldenSection />
      <FeedbackSection />
    </div>
  );
}

// Realized-episode backtest: score shock vectors against what markets actually
// did, using the historic series already in the store. The two modes are shown
// side by side and never merged into one number.
function BacktestSection() {
  const [modes, setModes] = useState<BacktestMode[]>([...BACKTEST_MODES]);
  const [jobId, setJobId] = useState<string | null>(null);
  const episodes = useBacktestEpisodes();
  const trigger = useTriggerBacktest();
  const job = useJob(jobId);
  const running = (jobId != null && !isTerminal(job.data?.status)) || trigger.isPending;
  const result = (isTerminal(job.data?.status) ? job.data?.result : null) as
    | BacktestResult
    | null;

  const toggle = (m: BacktestMode) =>
    setModes((cur) =>
      cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m],
    );

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Realized-episode backtest</strong>
      <span className="faint">
        Shock vectors scored against what markets actually did in past crises,
        measured from the historic series in the time-series store.
      </span>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {BACKTEST_MODES.map((m) => (
          <label key={m} className="row" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={modes.includes(m)}
              onChange={() => toggle(m)}
            />
            <span className="faint">{BACKTEST_MODE_LABEL[m]}</span>
          </label>
        ))}
        <button
          className="btn primary"
          disabled={running || modes.length === 0}
          onClick={() =>
            trigger.mutate({ modes }, { onSuccess: (j) => setJobId(j.id) })
          }
        >
          {running ? "Running…" : "Run backtest"}
        </button>
      </div>
      <Async q={episodes}>
        {episodes.data && (
          <span className="faint" style={{ fontSize: 12 }}>
            {episodes.data.total} episodes ·{" "}
            {episodes.data.episodes.filter((e) => e.fields.length > 0).length} with a
            realized reference in the current corpus
          </span>
        )}
      </Async>
      {trigger.isError && (
        <div className="callout bad">{(trigger.error as Error).message}</div>
      )}
      {jobId && <JobConsole jobId={jobId} />}
      {result && <BacktestResults result={result} />}
    </div>
  );
}

function BacktestResults({ result }: { result: BacktestResult }) {
  const modes = Object.keys(result.modes) as BacktestMode[];
  const exAnte = result.modes.ex_ante_skill;
  const exAnteAbstention = Number(
    result.summary.ex_ante_skill_abstention_rate ?? 0,
  );
  return (
    <div className="col" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      {modes.map((m) => (
        <div key={m} className="col" style={{ gap: 6 }}>
          <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
            {BACKTEST_MODE_LABEL[m] ?? m}
          </span>
          <div className="row" style={{ flexWrap: "wrap" }}>
            {Object.entries(result.modes[m].summary).map(([k, v]) => (
              <StatCard
                key={k}
                label={k}
                value={
                  k.startsWith("n_") ? String(v) : Number(v).toFixed(3)
                }
              />
            ))}
          </div>
          <EpisodeBreakdown rows={result.modes[m].rows} />
        </div>
      ))}
      {exAnte && exAnteAbstention > 0 && (
        <div className="callout">
          <strong>Ex-ante abstentions are a corpus-depth statement, not a quality
          failure.</strong>{" "}
          Retrieval is bounded to documents published before each episode opened; where
          the corpus holds nothing from that period, refusing is the correct behaviour.
        </div>
      )}
    </div>
  );
}

function EpisodeBreakdown({ rows }: { rows: BacktestRow[] }) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint" style={{ fontSize: 12 }}>
          {rows.length} episode{rows.length === 1 ? "" : "s"}
        </span>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                <Th>Episode</Th>
                <Th>Field</Th>
                <Th>Realized</Th>
                <Th>Emitted</Th>
                <Th>Sign</Th>
                <Th>In band</Th>
              </tr>
            </thead>
            <tbody>
              {rows.flatMap((r) =>
                r.abstained ? (
                  [
                    <tr key={r.episode} style={{ borderTop: "1px solid var(--border)" }}>
                      <Td>{r.aka}</Td>
                      <Td className="faint" colSpan={5}>
                        abstained
                        {r.pit_cutoff ? ` · no evidence before ${r.pit_cutoff}` : ""}
                      </Td>
                    </tr>,
                  ]
                ) : (
                  Object.entries(r.fields).map(([field, f], i) => (
                    <tr
                      key={`${r.episode}:${field}`}
                      style={{
                        borderTop: "1px solid var(--border)",
                        background: f.within_tol ? undefined : "var(--bg-elev-2)",
                      }}
                    >
                      <Td>{i === 0 ? r.aka : ""}</Td>
                      <Td>{field}</Td>
                      <Td>
                        {f.reference} <span className="faint">{f.unit}</span>
                      </Td>
                      <Td className={f.present ? "" : "faint"}>
                        {f.present ? f.emitted : "—"}
                      </Td>
                      <Td>
                        {f.sign_match === null ? (
                          <span className="faint">—</span>
                        ) : (
                          <span className={f.sign_match ? "badge good" : "badge bad"}>
                            {f.sign_match ? "ok" : "flipped"}
                          </span>
                        )}
                      </Td>
                      <Td>
                        <span className={f.within_tol ? "badge good" : "badge"}>
                          {f.within_tol ? "yes" : "no"}
                        </span>
                      </Td>
                    </tr>
                  ))
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Self-validation: differential-test the answer path against the numbers already
// in the time-series store. No golden file and no LLM judge — the cases are
// generated from the store, so the check grows with the corpus.
function SelfCheckSection() {
  const [perGroup, setPerGroup] = useState(2);
  const [maxCases, setMaxCases] = useState(100);
  const [jobId, setJobId] = useState<string | null>(null);
  const trigger = useTriggerSelfCheck();
  const job = useJob(jobId);
  const running = (jobId != null && !isTerminal(job.data?.status)) || trigger.isPending;
  const result = (isTerminal(job.data?.status) ? job.data?.result : null) as
    | SelfCheckResult
    | null;

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Self-validation</strong>
      <span className="faint">
        Questions generated from the time-series store, scored against the stored
        values — plus negative controls whose only correct answer is a refusal.
      </span>
      <div className="row">
        <label className="row" style={{ gap: 6 }}>
          <span className="faint">samples/series</span>
          <input
            className="input"
            style={{ width: 70 }}
            type="number"
            min={1}
            value={perGroup}
            onChange={(e) => setPerGroup(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <label className="row" style={{ gap: 6 }}>
          <span className="faint">max cases</span>
          <input
            className="input"
            style={{ width: 90 }}
            type="number"
            min={1}
            value={maxCases}
            onChange={(e) => setMaxCases(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <button
          className="btn primary"
          disabled={running}
          onClick={() =>
            trigger.mutate(
              { per_group: perGroup, max_cases: maxCases },
              { onSuccess: (j) => setJobId(j.id) },
            )
          }
        >
          {running ? "Running…" : "Run self-check"}
        </button>
      </div>
      {trigger.isError && (
        <div className="callout bad">{(trigger.error as Error).message}</div>
      )}
      {jobId && <JobConsole jobId={jobId} />}
      {result && <SelfCheckResults result={result} />}
    </div>
  );
}

function SelfCheckResults({ result }: { result: SelfCheckResult }) {
  const s = result.summary ?? {};
  const fabricated = Number(s.fabrication_rate ?? 0);
  const agreement = Number(s.numeric_agreement ?? 0);
  return (
    <div className="col" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {Object.entries(s).map(([m, v]) => (
          <StatCard
            key={m}
            label={m + (SELFCHECK_LOWER_BETTER.has(m) ? " ↓" : "")}
            value={m.startsWith("n_") ? String(v) : Number(v).toFixed(3)}
          />
        ))}
      </div>
      {fabricated > 0 ? (
        <div className="callout bad">
          <strong>{(fabricated * 100).toFixed(0)}% of negative controls were answered
          with a fabricated number.</strong> These probes name scenarios or series that
          do not exist in the corpus — the correct response is a refusal.
        </div>
      ) : (
        <div className="callout">
          No fabrications: every negative control was refused or corrected.
        </div>
      )}
      {agreement < 1 && (
        <div className="faint" style={{ fontSize: 12 }}>
          <strong>statement_miss</strong> = the right series was retrieved but the prose
          quoted a different number (generation).{" "}
          <strong>hydration_miss</strong> = the prose was right but the structured path
          missed the row (retrieval / store).
        </div>
      )}
      <NumericBreakdown rows={result.numeric?.rows ?? []} />
      <ProbeBreakdown rows={result.probes?.rows ?? []} />
    </div>
  );
}

function NumericBreakdown({ rows }: { rows: SelfCheckNumericRow[] }) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  const failed = rows.filter((r) => r.status !== "ok").length;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
          Numeric cases · {rows.length}
          {failed > 0 ? ` · ${failed} not ok` : ""}
        </span>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                <Th>Series</Th>
                <Th>Period</Th>
                <Th>Expected</Th>
                <Th>Hydrated</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.case_id}
                  style={{
                    borderTop: "1px solid var(--border)",
                    background: r.status !== "ok" ? "var(--bg-elev-2)" : undefined,
                  }}
                >
                  <Td>
                    {r.variable}
                    {r.scenario ? <span className="faint"> · {r.scenario}</span> : null}
                  </Td>
                  <Td className="faint">{r.period}</Td>
                  <Td>{r.expected}</Td>
                  <Td className={r.hydrated ? "" : "faint"}>
                    {r.hydrated_value ?? "—"}
                  </Td>
                  <Td>
                    <span className={r.status === "ok" ? "badge" : "badge bad"}>
                      {r.status}
                    </span>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ProbeBreakdown({ rows }: { rows: SelfCheckProbeRow[] }) {
  const [open, setOpen] = useState(false);
  if (rows.length === 0) return null;
  const bad = rows.filter((r) => r.outcome === "fabricated").length;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
          Negative controls · {rows.length}
          {bad > 0 ? ` · ${bad} fabricated` : ""}
        </span>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                <Th>Probe</Th>
                <Th>Kind</Th>
                <Th>Outcome</Th>
                <Th>Answer</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.case_id}
                  style={{
                    borderTop: "1px solid var(--border)",
                    background:
                      r.outcome === "fabricated" ? "var(--bg-elev-2)" : undefined,
                  }}
                >
                  <Td>
                    {r.question}
                    <br />
                    <span className="faint" style={{ fontSize: 12 }}>
                      {r.detail}
                    </span>
                  </Td>
                  <Td className="faint">{r.kind}</Td>
                  <Td>
                    <span
                      className={r.outcome === "fabricated" ? "badge bad" : "badge"}
                    >
                      {r.outcome}
                    </span>
                  </Td>
                  <Td className="faint" style={{ fontSize: 12 }}>
                    {r.answer_excerpt || "—"}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function EvalSection() {
  const [k, setK] = useState(8);
  const [judge, setJudge] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const trigger = useTriggerEval();
  const job = useJob(jobId);
  const running = (jobId != null && !isTerminal(job.data?.status)) || trigger.isPending;
  const result = (isTerminal(job.data?.status) ? job.data?.result : null) as
    | EvalResult
    | null;

  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Evaluation</strong>
      <div className="row">
        <label className="row" style={{ gap: 6 }}>
          <span className="faint">k</span>
          <input
            className="input"
            style={{ width: 70 }}
            type="number"
            min={1}
            value={k}
            onChange={(e) => setK(Math.max(1, Number(e.target.value) || 1))}
          />
        </label>
        <button
          className="btn primary"
          disabled={running}
          onClick={() =>
            trigger.mutate({ k, judge }, { onSuccess: (j) => setJobId(j.id) })
          }
        >
          {running ? "Running…" : "Run eval"}
        </button>
        <label
          className="row"
          style={{ gap: 6, fontSize: 13 }}
          title="also run the full pipeline per golden question and score the answers with the LLM judge"
        >
          <input type="checkbox" checked={judge} onChange={(e) => setJudge(e.target.checked)} />
          <span>LLM judge (faithfulness + relevancy)</span>
        </label>
      </div>
      {judge && (
        <span className="faint" style={{ fontSize: 12 }}>
          The judge pass runs a full query per golden question — billable LLM calls and
          several minutes on a CPU host. Its means join the summary, so drift can gate on them.
        </span>
      )}
      {trigger.isError && (
        <div className="callout bad">{(trigger.error as Error).message}</div>
      )}
      {jobId && <JobConsole jobId={jobId} />}
      {result && <EvalResults result={result} />}
    </div>
  );
}

function EvalResults({ result }: { result: EvalResult }) {
  const setBaseline = useSetBaseline();
  return (
    <div className="col" style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <strong>Results @k={result.k}</strong>
        <button
          className="btn"
          disabled={setBaseline.isPending}
          onClick={() => setBaseline.mutate(result.summary)}
        >
          {setBaseline.isSuccess ? "Baseline set ✓" : "Promote to baseline"}
        </button>
      </div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {Object.entries(result.summary).map(([m, v]) => (
          <StatCard key={m} label={m} value={v.toFixed(3)} />
        ))}
      </div>
      {result.regressions.length > 0 ? (
        <div className="callout bad">
          <strong>Drift detected vs baseline:</strong>
          <ul style={{ margin: "6px 0 0" }}>
            {result.regressions.map((r) => (
              <li key={r.metric}>
                {r.metric}: {r.current.toFixed(3)} vs {r.baseline.toFixed(3)} (Δ{" "}
                {r.delta.toFixed(3)})
              </li>
            ))}
          </ul>
        </div>
      ) : result.baseline ? (
        <div className="callout">No regressions vs the saved baseline.</div>
      ) : (
        <div className="faint">No baseline saved yet — promote a run to compare against.</div>
      )}

      {result.rows && result.rows.length > 0 && (
        <PerQuestionBreakdown result={result} />
      )}
      {result.judge_rows && result.judge_rows.length > 0 && (
        <JudgeBreakdown rows={result.judge_rows} />
      )}
    </div>
  );
}

// Per-question eval drill-down: which golden questions passed/failed at hit@k, plus
// their recall/mrr and the retrieved keys vs the gold set.
function PerQuestionBreakdown({ result }: { result: EvalResult }) {
  const [open, setOpen] = useState(false);
  const rows = result.rows as unknown as EvalRow[];
  const metrics = Object.keys(result.summary);
  const hitKey = metrics.find((m) => m.startsWith("hit@"));
  const failed = hitKey ? rows.filter((r) => Number(r[hitKey]) < 1).length : 0;

  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
          Per-question breakdown{failed > 0 ? ` · ${failed} below hit@k` : ""}
        </span>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                <Th>Question</Th>
                {metrics.map((m) => (
                  <Th key={m}>{m}</Th>
                ))}
                <Th>Gold vs top</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const miss = hitKey ? Number(r[hitKey]) < 1 : false;
                return (
                  <tr
                    key={i}
                    style={{
                      borderTop: "1px solid var(--border)",
                      background: miss ? "var(--bg-elev-2)" : undefined,
                    }}
                  >
                    <Td>{r.question}</Td>
                    {metrics.map((m) => (
                      <Td key={m} className={Number(r[m]) > 0 ? "" : "faint"}>
                        {fmtMetric(r[m])}
                      </Td>
                    ))}
                    <Td className="faint" style={{ fontSize: 12 }}>
                      <span>gold: {(r.gold ?? []).join(", ") || "—"}</span>
                      <br />
                      <span>top: {(r.top ?? []).slice(0, 5).join(", ") || "—"}</span>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// Per-question LLM-judge scores, lowest faithfulness first (the ones to read).
function JudgeBreakdown({ rows }: { rows: JudgeRow[] }) {
  const [open, setOpen] = useState(false);
  const sorted = [...rows].sort((a, b) => a.faithfulness - b.faithfulness);
  const weak = rows.filter((r) => r.faithfulness < 0.5).length;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint">
          LLM judge · {rows.length} answers · {weak} with faithfulness &lt; 0.5
        </span>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      {open && (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
              <Th>Question</Th>
              <Th>Faithful</Th>
              <Th>Relevant</Th>
              <Th>Judge reason</Th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                <Td>{r.question}</Td>
                <Td style={{ color: r.faithfulness < 0.5 ? "var(--bad)" : undefined }}>
                  {r.faithfulness.toFixed(2)}
                </Td>
                <Td>{r.relevancy.toFixed(2)}</Td>
                <Td className="faint">{r.reason}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// §B structured-output health: how often each task/model's JSON needed repair or
// failed schema validation since the API process started. A rising repair rate for
// one task/model is the early sign of model drift on structured output.
function SchemaHealthSection() {
  const q = useSchemaHealth();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Structured-output health</strong>
      <span className="faint" style={{ fontSize: 12 }}>
        Schema-validated LLM calls since the API process started (resets on restart).
      </span>
      <Async q={q}>
        {q.data &&
          (q.data.totals.total === 0 ? (
            <p className="muted">No structured-output calls recorded yet.</p>
          ) : (
            <>
              <div className="row" style={{ flexWrap: "wrap" }}>
                <StatCard label="Calls" value={q.data.totals.total.toLocaleString()} />
                <StatCard label="Repaired" value={q.data.totals.repaired.toLocaleString()} />
                <StatCard
                  label="Failed"
                  value={q.data.totals.failed.toLocaleString()}
                  tone={q.data.totals.failed > 0 ? "warn" : "good"}
                />
                <StatCard
                  label="Repair rate"
                  value={`${(q.data.totals.repair_rate * 100).toFixed(1)}%`}
                />
              </div>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                    <Th>Task</Th>
                    <Th>Model</Th>
                    <Th>OK</Th>
                    <Th>Repaired</Th>
                    <Th>Failed</Th>
                    <Th>Repair rate</Th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(q.data.by_task).flatMap(([task, models]) =>
                    Object.entries(models).map(([model, c]) => (
                      <tr key={`${task}/${model}`} style={{ borderTop: "1px solid var(--border)" }}>
                        <Td>{task}</Td>
                        <Td className="faint">{model}</Td>
                        <Td>{c.ok}</Td>
                        <Td>{c.repaired}</Td>
                        <Td style={{ color: c.failed > 0 ? "var(--bad)" : undefined }}>{c.failed}</Td>
                        <Td>{(c.repair_rate * 100).toFixed(1)}%</Td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </>
          ))}
      </Async>
    </div>
  );
}

function fmtMetric(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(3) : String(v ?? "—");
}

function HistorySection() {
  const q = useEvalHistory();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Eval history</strong>
      <Async q={q}>
        {q.data &&
          (q.data.runs.length === 0 ? (
            <p className="muted">No eval runs recorded yet — run an eval above.</p>
          ) : (
            <HistoryBody runs={q.data.runs} />
          ))}
      </Async>
    </div>
  );
}

function HistoryBody({ runs }: { runs: EvalHistoryRun[] }) {
  // union of metric names across runs, stable order
  const metrics = Array.from(
    runs.reduce((set, r) => {
      Object.keys(r.summary ?? {}).forEach((m) => set.add(m));
      return set;
    }, new Set<string>()),
  );
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
            <Th>When</Th>
            <Th>k</Th>
            {metrics.map((m) => (
              <Th key={m}>{m}</Th>
            ))}
            <Th>Regressions</Th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r, i) => (
            <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
              <Td className="faint">{(r.ts ?? "").replace("T", " ").slice(0, 19)}</Td>
              <Td>{r.k ?? "—"}</Td>
              {metrics.map((m) => (
                <Td key={m}>{fmtMetric(r.summary?.[m])}</Td>
              ))}
              <Td className={r.n_regressions ? "" : "faint"}>
                {r.n_regressions
                  ? <span className="badge bad">{r.n_regressions}</span>
                  : "0"}
              </Td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BaselineSection() {
  const q = useEvalBaseline();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <strong>Drift baseline</strong>
      <Async q={q}>
        {q.data &&
          (q.data.baseline ? (
            <div className="row" style={{ flexWrap: "wrap" }}>
              {Object.entries(q.data.baseline).map(([m, v]) => (
                <StatCard key={m} label={m} value={v.toFixed(3)} />
              ))}
            </div>
          ) : (
            <p className="muted">No baseline saved. Run an eval and promote it.</p>
          ))}
      </Async>
    </div>
  );
}

function GoldenSection() {
  const q = useGolden();
  const [open, setOpen] = useState(false);
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Golden set</strong>
        <button className="chip" onClick={() => setOpen((v) => !v)}>
          {open ? "hide" : "show"}
        </button>
      </div>
      <Async q={q}>
        {q.data && (
          <>
            <span className="faint">{q.data.questions.length} questions · {q.data.path}</span>
            {open && (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                    <Th>Question</Th>
                    <Th>Expected</Th>
                    <Th>Match field</Th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.questions.map((g: GoldenQuestion, i) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      <Td>{g.question}</Td>
                      <Td className="faint">{(g.expected ?? []).join(", ")}</Td>
                      <Td className="faint">{g.match_field ?? "source_id"}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </Async>
    </div>
  );
}

function FeedbackSection() {
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const q = useFeedback(limit, offset);
  const reconcile = useReconcileFeedback();
  return (
    <div className="card col" style={{ marginTop: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Feedback inbox</strong>
        <Async q={q}>
          {q.data && (
            <div className="row" style={{ gap: 6 }}>
              <span className="faint">{q.data.total} total</span>
              <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹</button>
              <button className="btn" disabled={offset + limit >= q.data.total} onClick={() => setOffset(offset + limit)}>›</button>
            </div>
          )}
        </Async>
      </div>
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <button
          className="btn"
          disabled={reconcile.isPending}
          onClick={() => reconcile.mutate()}
          title="turn positive, chunk-citing feedback into candidate golden entries for review"
        >
          {reconcile.isPending ? "Proposing…" : "Propose golden candidates"}
        </button>
        <span className="faint" style={{ fontSize: 12 }}>
          Writes a review file; nothing is promoted into the golden set automatically.
        </span>
      </div>
      {reconcile.isError && (
        <div className="callout bad">{(reconcile.error as Error).message}</div>
      )}
      {reconcile.data && (
        <div className="callout">
          {reconcile.data.n_proposed === 0 ? (
            "No new candidates — no positive, chunk-citing feedback outside the golden set."
          ) : (
            <>
              <strong>{reconcile.data.n_proposed} candidate(s)</strong> written to{" "}
              <code>{reconcile.data.written_to}</code> — review and promote by hand.
              <ul style={{ margin: "6px 0 0" }}>
                {reconcile.data.candidates.map((c, i) => (
                  <li key={i}>
                    {c.question}{" "}
                    <span className="faint">({c.expected.length} chunk ids)</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      <Async q={q}>
        {q.data &&
          (q.data.items.length === 0 ? (
            <p className="muted">No feedback recorded yet.</p>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-faint)" }}>
                  <Th>When</Th>
                  <Th>Rating</Th>
                  <Th>Question</Th>
                  <Th>Note</Th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((f: FeedbackItem) => (
                  <tr key={f.i} style={{ borderTop: "1px solid var(--border)" }}>
                    <Td className="faint">{(f.ts ?? "").replace("T", " ").slice(0, 19)}</Td>
                    <Td>{f.rating === 1 ? "👍" : f.rating === -1 ? "👎" : "—"}</Td>
                    <Td>{f.question ?? "—"}</Td>
                    <Td className="faint">{f.note ?? ""}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
      </Async>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return <th style={{ padding: "10px 14px", fontWeight: 600, fontSize: 12 }}>{children}</th>;
}
function Td({
  children,
  className,
  style,
  colSpan,
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  colSpan?: number;
}) {
  return (
    <td
      className={className}
      colSpan={colSpan}
      style={{ padding: "10px 14px", fontSize: 13, ...style }}
    >
      {children}
    </td>
  );
}
