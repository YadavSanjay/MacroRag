// Scaffolded route placeholder. Each names its roadmap phase and backend dependency
// so the build-out order from the design doc is visible in-app.
export function Placeholder({
  title,
  phase,
  blurb,
  needs,
}: {
  title: string;
  phase: string;
  blurb: string;
  needs?: string;
}) {
  return (
    <div className="content">
      <h1 className="page-title">{title}</h1>
      <p className="page-sub">{blurb}</p>
      <div className="placeholder">
        <div className="col" style={{ alignItems: "center" }}>
          <span className="badge">{phase}</span>
          <p className="muted" style={{ maxWidth: 460 }}>
            This screen is scaffolded and routed. Implementation is planned in the UI
            design doc (<code>docs/design/Macro_RAG_UI_Design.md</code>).
          </p>
          {needs && <p className="faint">Backend dependency: {needs}</p>}
        </div>
      </div>
    </div>
  );
}
