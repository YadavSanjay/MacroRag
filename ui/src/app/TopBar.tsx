import { useState } from "react";
import { useHealth } from "@/api/hooks";
import { ApiKeyDialog } from "./ApiKeyGate";
import { getApiKey } from "@/api/client";
import { ThemeToggle } from "@/components/ThemeToggle";

export function TopBar() {
  const { data, isError } = useHealth();
  const [keyOpen, setKeyOpen] = useState(false);
  const hasKey = !!getApiKey();

  const worldView =
    (data?.index as Record<string, unknown> | undefined)?.["world_view_id"] ??
    (data?.index as Record<string, unknown> | undefined)?.["built_at"];

  return (
    <header className="topbar">
      <span className="badge env">dev</span>

      {isError ? (
        <span className="badge bad">● api offline</span>
      ) : data ? (
        <span className={`badge ${data.built ? "good" : "warn"}`}>
          ● {data.built ? "ready" : "not built"}
        </span>
      ) : (
        <span className="badge">● connecting…</span>
      )}

      {worldView ? (
        <span className="badge" title="corpus snapshot the index was built from">
          {String(worldView)}
        </span>
      ) : null}

      <div className="spacer" />

      <ThemeToggle />

      <button className="btn" onClick={() => setKeyOpen(true)}>
        {hasKey ? "API key ✓" : "Set API key"}
      </button>

      {keyOpen && <ApiKeyDialog onClose={() => setKeyOpen(false)} />}
    </header>
  );
}
