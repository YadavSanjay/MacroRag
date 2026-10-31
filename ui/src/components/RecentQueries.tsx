// Compact recent-query/prompt history, click an entry to re-run it. Backed by
// useHistory (localStorage) — the caller owns add(); this only renders + picks.
export function RecentQueries({
  items,
  onPick,
  onClear,
  label = "Recent",
}: {
  items: string[];
  onPick: (q: string) => void;
  onClear: () => void;
  label?: string;
}) {
  if (items.length === 0) return null;
  return (
    <div className="card col" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
          {label}
        </span>
        <button className="chip" onClick={onClear}>
          clear
        </button>
      </div>
      <div className="col" style={{ gap: 4 }}>
        {items.map((q) => (
          <button
            key={q}
            className="btn"
            onClick={() => onPick(q)}
            style={{
              textAlign: "left",
              justifyContent: "flex-start",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              maxWidth: "100%",
            }}
            title={q}
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
