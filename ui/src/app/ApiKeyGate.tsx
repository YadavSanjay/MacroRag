import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getApiKey, setApiKey } from "@/api/client";
import { Drawer } from "@/components/Drawer";

// The API uses optional X-API-Key auth (per-key principal + rate limit). Auth is only
// enforced when the backend sets auth.enabled=true, so the key is optional in dev.
export function ApiKeyDialog({ onClose }: { onClose: () => void }) {
  const [value, setValue] = useState(getApiKey() ?? "");
  const qc = useQueryClient();

  function save() {
    setApiKey(value.trim() || null);
    qc.invalidateQueries();
    onClose();
  }

  return (
    <Drawer title="API key" onClose={onClose}>
      <div className="col">
        <p className="muted">
          Sent as the <code>X-API-Key</code> header. Required only when the backend has
          <code> auth.enabled=true</code>; leave blank for an open dev server. The key
          determines your principal and rate limit.
        </p>
        <input
          className="input"
          type="password"
          placeholder="paste API key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          autoFocus
        />
        <div className="row">
          <button className="btn primary" onClick={save}>
            Save
          </button>
          <button
            className="btn"
            onClick={() => {
              setValue("");
            }}
          >
            Clear
          </button>
        </div>
      </div>
    </Drawer>
  );
}
