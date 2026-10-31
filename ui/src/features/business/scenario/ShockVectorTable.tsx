import type { MissingField, ShockField, ShockSchemaField } from "@/api/types";
import { CitationChip } from "@/components/Citations";
import { citationIndex } from "@/lib/citations";
import {
  SHOCK_FIELDS,
  SHOCK_GROUPS,
  isHardCode,
  shockSpec,
  validationHint,
  type ShockGroup,
} from "@/lib/shockVocab";

type Vector = Record<string, ShockField>;
// field -> authoritative sign convention from GET /synthesis/schema (when loaded).
type SignMap = Map<string, string>;

// What the request asked for, so an absent field can say why it is absent.
export interface VectorRequestContext {
  requested?: string[]; // synthesis.fields (empty/undefined = all)
  missing?: MissingField[]; // synthesis_diagnostics.missing_fields
}

// Renders the full AITrade contract grouped by risk class. Populated fields (the
// ones that passed the citation gate — each cites >=1 dated chunk) are shown active;
// the rest are muted "not synthesized" so the gating outcome is visible. `schema`,
// when supplied, provides the authoritative unit/sign legend (falls back to the
// static vocab used for grouping/ordering).
export function ShockVectorTable({
  vector,
  schema,
  request,
}: {
  vector: Vector;
  schema?: ShockSchemaField[];
  request?: VectorRequestContext;
}) {
  const present = Object.keys(vector);
  const known = new Set(SHOCK_FIELDS.map((s) => s.field));
  const unknown = present.filter((f) => !known.has(f));
  const signs: SignMap = new Map((schema ?? []).map((f) => [f.name, f.positive_means]));
  const absent = absentReasons(request);

  // Number cited sources in table order (grouped fields, then unknown fields).
  const cites = citationIndex([
    ...SHOCK_FIELDS.map((s) => vector[s.field]?.chunk_ids ?? []),
    ...unknown.map((f) => vector[f]?.chunk_ids ?? []),
  ]);

  return (
    <div className="col">
      {SHOCK_GROUPS.map((group) => (
        <GroupBlock
          key={group}
          group={group}
          vector={vector}
          signs={signs}
          cites={cites}
          absent={absent}
        />
      ))}
      {unknown.length > 0 && (
        <div className="card col">
          <strong>Other (not in contract vocabulary)</strong>
          {unknown.map((f) => (
            <FieldRow key={f} field={f} sf={vector[f]} signs={signs} cites={cites} absent={absent} />
          ))}
        </div>
      )}
    </div>
  );
}

function GroupBlock({
  group,
  vector,
  signs,
  cites,
  absent,
}: {
  group: ShockGroup;
  vector: Vector;
  signs: SignMap;
  cites: Map<string, number>;
  absent: (field: string) => string;
}) {
  const fields = SHOCK_FIELDS.filter((s) => s.group === group);
  const activeCount = fields.filter((s) => vector[s.field]).length;

  return (
    <div className="card col">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>{group}</strong>
        <span className="faint">
          {activeCount}/{fields.length} grounded
        </span>
      </div>
      {fields.map((s) => (
        <FieldRow
          key={s.field}
          field={s.field}
          sf={vector[s.field]}
          signs={signs}
          cites={cites}
          absent={absent}
        />
      ))}
    </div>
  );
}

function FieldRow({
  field,
  sf,
  signs,
  cites,
  absent,
}: {
  field: string;
  sf: ShockField | undefined;
  signs: SignMap;
  cites: Map<string, number>;
  absent: (field: string) => string;
}) {
  const spec = shockSpec(field);
  const positiveMeans = signs.get(field) ?? spec?.positiveMeans;
  const active = !!sf;

  return (
    <div
      className="row"
      style={{
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 12,
        padding: "8px 10px",
        borderRadius: 6,
        background: active ? "var(--bg-elev-2)" : "transparent",
        opacity: active ? 1 : 0.55,
      }}
    >
      <div className="col" style={{ gap: 4, minWidth: 0, flex: 1 }}>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <code style={{ fontWeight: 700 }}>{field}</code>
          {positiveMeans && (
            <span className="faint" style={{ fontSize: 11 }}>
              + = {positiveMeans}
            </span>
          )}
        </div>
        {active && <FieldFlags sf={sf!} />}
        {active && sf!.rationale && (
          <span className="muted" style={{ fontSize: 13 }}>
            {sf!.rationale}
          </span>
        )}
        {active && (
          <span className="tag-list">
            {sf!.chunk_ids.map((c) => (
              <CitationChip key={c} chunkId={c} index={cites.get(c) ?? 0} />
            ))}
          </span>
        )}
        {!active && (
          <span className="faint" style={{ fontSize: 12 }}>
            {absent(field)}
          </span>
        )}
      </div>

      <div style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        {active ? (
          <>
            <div style={{ fontSize: 18, fontWeight: 700 }}>
              {fmtSigned(sf!.value)}
            </div>
            <div className="faint" style={{ fontSize: 11 }}>
              {sf!.unit}
            </div>
          </>
        ) : (
          <div className="faint">—</div>
        )}
      </div>
    </div>
  );
}

// Grounding mode, evidence horizon and validation codes for a kept field. Hard
// codes only survive here when synthesis.drop_invalid is off — shown as "bad".
function FieldFlags({ sf }: { sf: ShockField }) {
  const codes = sf.validation ?? [];
  const analog = sf.grounding_mode === "analog";
  if (!analog && sf.source_horizon_days == null && codes.length === 0) return null;
  return (
    <span className="tag-list">
      {analog && (
        <span
          className="badge warn"
          title="anchored to a historical analog episode or official adverse path — down-weight vs direct"
        >
          analog
        </span>
      )}
      {sf.source_horizon_days != null && (
        <span className="badge" title="horizon of the move the cited evidence describes">
          evidence {sf.source_horizon_days}d
        </span>
      )}
      {codes.map((c) => (
        <span
          key={c}
          className={isHardCode(c) ? "badge bad" : "badge warn"}
          title={`${isHardCode(c) ? "hard" : "soft"} flag${validationHint(c) ? ` — ${validationHint(c)}` : ""}`}
        >
          {c}
        </span>
      ))}
    </span>
  );
}

const ABSENT_DEFAULT = "not synthesized — no grounded evidence passed the citation gate";

// field -> one-line reason it is absent, from synthesis_diagnostics.missing_fields
// when the caller restricted the fields, else the generic gate message.
function absentReasons(request?: VectorRequestContext): (field: string) => string {
  const requested = new Set((request?.requested ?? []).map((f) => f.toUpperCase()));
  const missing = new Map((request?.missing ?? []).map((m) => [m.field, m]));
  return (field) => {
    const m = missing.get(field);
    if (m) {
      const why =
        m.reason === "dropped"
          ? `dropped${m.codes.length ? ` (${m.codes.join(", ")})` : ""}`
          : "not emitted — the model found no usable evidence";
      return m.card_field ? why : `${why} · no episode card measures it, prose only`;
    }
    if (requested.size > 0 && !requested.has(field)) return "not requested";
    return ABSENT_DEFAULT;
  };
}

function fmtSigned(v: number): string {
  const s = v > 0 ? "+" : "";
  return `${s}${v}`;
}
