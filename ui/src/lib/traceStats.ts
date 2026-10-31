import type { TraceRecord } from "@/api/admin";

export function traceTotalMs(t: TraceRecord): number {
  const tm = t.timings_ms ?? {};
  return Object.values(tm).reduce((a, b) => a + (Number(b) || 0), 0);
}

export interface TraceSummary {
  n: number;
  refusalRate: number;
  avgMs: number;
  p95Ms: number;
  routeMix: { route: string; count: number }[];
}

export function summarizeTraces(traces: TraceRecord[]): TraceSummary {
  const n = traces.length;
  if (n === 0) {
    return { n: 0, refusalRate: 0, avgMs: 0, p95Ms: 0, routeMix: [] };
  }
  const refused = traces.filter((t) => t.refused).length;
  const totals = traces.map(traceTotalMs).filter((v) => v > 0);
  const avgMs = totals.length
    ? totals.reduce((a, b) => a + b, 0) / totals.length
    : 0;

  const sorted = [...totals].sort((a, b) => a - b);
  const p95Ms = sorted.length ? sorted[Math.floor(sorted.length * 0.95) === sorted.length ? sorted.length - 1 : Math.floor(sorted.length * 0.95)] : 0;

  const byRoute = new Map<string, number>();
  for (const t of traces) {
    const r = t.route ?? "—";
    byRoute.set(r, (byRoute.get(r) ?? 0) + 1);
  }
  const routeMix = [...byRoute.entries()]
    .map(([route, count]) => ({ route, count }))
    .sort((a, b) => b.count - a.count);

  return { n, refusalRate: refused / n, avgMs, p95Ms, routeMix };
}
