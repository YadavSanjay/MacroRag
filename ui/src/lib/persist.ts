// Tiny localStorage-backed persistence for UI workflow state (recent queries,
// pinned scenarios, saved filter views). Deliberately dependency-free and
// best-effort — a quota/serialization failure degrades to in-memory only.

import { useCallback, useState } from "react";

const NS = "macrorag.";

export function loadJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(NS + key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function saveJson<T>(key: string, value: T): void {
  try {
    localStorage.setItem(NS + key, JSON.stringify(value));
  } catch {
    /* quota exceeded / serialization error — ignore, stay in-memory */
  }
}

// useState that mirrors into localStorage on every set.
export function useLocalState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => loadJson(key, initial));
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      setValue((prev) => {
        const v =
          typeof next === "function" ? (next as (p: T) => T)(prev) : next;
        saveJson(key, v);
        return v;
      });
    },
    [key],
  );
  return [value, set] as const;
}

// A capped, de-duplicated most-recent-first string history (recent questions/prompts).
export function useHistory(key: string, max = 25) {
  const [items, setItems] = useLocalState<string[]>(key, []);
  const add = useCallback(
    (entry: string) => {
      const e = entry.trim();
      if (!e) return;
      setItems((prev) => [e, ...prev.filter((x) => x !== e)].slice(0, max));
    },
    [max, setItems],
  );
  const clear = useCallback(() => setItems([]), [setItems]);
  return { items, add, clear };
}
