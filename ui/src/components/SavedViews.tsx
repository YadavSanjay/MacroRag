import { useCallback } from "react";
import type { FilterModel } from "@/api/types";
import { useLocalState } from "@/lib/persist";
import { activeFilterCount } from "@/components/FacetFilterEditor";

export interface SavedView {
  name: string;
  filters: FilterModel;
}

// Named filter sets, persisted in localStorage under one shared key so a view
// saved on Ask is available on Factors and Scenario Studio too.
export function useSavedViews() {
  const [views, setViews] = useLocalState<SavedView[]>("savedViews", []);
  const save = useCallback(
    (name: string, filters: FilterModel) => {
      const n = name.trim();
      if (!n) return;
      setViews((prev) =>
        [{ name: n, filters }, ...prev.filter((v) => v.name !== n)].slice(0, 30),
      );
    },
    [setViews],
  );
  const remove = useCallback(
    (name: string) => setViews((prev) => prev.filter((v) => v.name !== name)),
    [setViews],
  );
  return { views, save, remove };
}

export function SavedViews({
  current,
  onApply,
}: {
  current: FilterModel;
  onApply: (f: FilterModel) => void;
}) {
  const { views, save, remove } = useSavedViews();
  const n = activeFilterCount(current);

  function onSave() {
    const name = window.prompt("Save current filters as…");
    if (name) save(name, current);
  }

  return (
    <div className="row" style={{ flexWrap: "wrap", gap: 6, alignItems: "center" }}>
      <span className="faint" style={{ fontSize: 12, textTransform: "uppercase" }}>
        Saved views
      </span>
      {views.length === 0 && (
        <span className="faint" style={{ fontSize: 12 }}>
          none yet
        </span>
      )}
      {views.map((v) => (
        <span key={v.name} className="row" style={{ gap: 2 }}>
          <button
            className="chip"
            onClick={() => onApply(v.filters)}
            title="apply this saved filter set"
          >
            {v.name}
          </button>
          <button
            className="chip"
            onClick={() => remove(v.name)}
            title="delete"
            style={{ padding: "1px 5px" }}
          >
            ×
          </button>
        </span>
      ))}
      <button className="btn" onClick={onSave} disabled={n === 0}>
        Save current{n ? ` (${n})` : ""}
      </button>
    </div>
  );
}
