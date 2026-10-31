// Navigation model shared by the rail and the router.

export interface NavItem {
  to: string;
  label: string;
  glyph: string;
  /** P0 = implemented now; others are scaffolded placeholders per the roadmap. */
  ready?: boolean;
}

export interface NavGroup {
  area: "business" | "maintenance" | "system";
  label: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    area: "business",
    label: "Business",
    items: [
      { to: "/ask", label: "Ask", glyph: "◈", ready: true },
      { to: "/factors", label: "Factors", glyph: "◎", ready: true },
      { to: "/scenario", label: "Scenario Studio", glyph: "⚡", ready: true },
      { to: "/corpus", label: "Corpus", glyph: "▤", ready: true },
    ],
  },
  {
    area: "maintenance",
    label: "Internal maintenance",
    items: [
      { to: "/ingestion", label: "Ingestion", glyph: "⇩", ready: true },
      { to: "/curation", label: "Curation", glyph: "✎", ready: true },
      { to: "/index", label: "Index", glyph: "▦", ready: true },
      { to: "/quality", label: "Quality", glyph: "✓", ready: true },
    ],
  },
  {
    area: "system",
    label: "System",
    items: [
      { to: "/system/map", label: "Map", glyph: "◇", ready: true },
      { to: "/system/traces", label: "Traces", glyph: "≋", ready: true },
      { to: "/system/health", label: "Health", glyph: "♥", ready: true },
      { to: "/system/stores", label: "Stores", glyph: "⛁", ready: true },
    ],
  },
];
