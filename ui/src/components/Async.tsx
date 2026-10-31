import type { ReactNode } from "react";
import { ApiError } from "@/api/client";

interface AsyncLike {
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}

// Standard loading/error gate for a query-backed view. Renders children only on
// success; surfaces a hint when /admin/* returns 401/403 (auth gate).
export function Async({
  q,
  children,
  loadingText = "Loading…",
}: {
  q: AsyncLike;
  children: ReactNode;
  loadingText?: string;
}) {
  if (q.isLoading) return <p className="muted">{loadingText}</p>;
  if (q.isError) {
    const e = q.error;
    const status = e instanceof ApiError ? e.status : undefined;
    const msg = e instanceof Error ? e.message : String(e);
    return (
      <div className="callout bad">
        {msg}
        {(status === 401 || status === 403) && (
          <div className="faint" style={{ marginTop: 6 }}>
            The /admin surface requires an admin API key — set one from the top bar.
          </div>
        )}
      </div>
    );
  }
  return <>{children}</>;
}

export function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "good" | "warn" | "bad";
}) {
  const color =
    tone === "good"
      ? "var(--good)"
      : tone === "warn"
        ? "var(--warn)"
        : tone === "bad"
          ? "var(--bad)"
          : "var(--text)";
  return (
    <div className="card" style={{ minWidth: 160, flex: 1 }}>
      <div className="overline">{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color }}>{value}</div>
      {sub && <div className="faint" style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}
