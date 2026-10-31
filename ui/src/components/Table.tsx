import type { ReactNode } from "react";

// Shared data table used by Corpus, Index, and any tabular admin view. Styling
// lives in the .data-table / .table-card classes (global.css) so every table
// shares one padding, type, hairline, and hover treatment.
export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="card table-card">
      <table className="data-table">{children}</table>
    </div>
  );
}

export function Th({ children }: { children: ReactNode }) {
  return <th>{children}</th>;
}

export function Td({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <td className={className}>{children}</td>;
}
