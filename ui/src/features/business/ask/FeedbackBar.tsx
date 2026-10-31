import { useState } from "react";
import { useFeedback } from "@/api/hooks";

// Writes to POST /feedback (grows the golden set). Captures rating + note + the
// chunks the user found relevant.
export function FeedbackBar({
  question,
  chunkIds,
}: {
  question: string;
  chunkIds: string[];
}) {
  const fb = useFeedback();
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [note, setNote] = useState("");

  if (fb.isSuccess) {
    return <div className="callout">Thanks — feedback recorded.</div>;
  }

  function submit(r: 1 | -1) {
    setRating(r);
    fb.mutate({ question, rating: r, note: note.trim() || null, chunk_ids: chunkIds });
  }

  return (
    <div className="card row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
      <div className="row">
        <button
          className={`btn ${rating === 1 ? "primary" : ""}`}
          disabled={fb.isPending}
          onClick={() => submit(1)}
        >
          👍 Helpful
        </button>
        <button
          className={`btn ${rating === -1 ? "primary" : ""}`}
          disabled={fb.isPending}
          onClick={() => submit(-1)}
        >
          👎 Off
        </button>
      </div>
      <input
        className="input"
        style={{ maxWidth: 360 }}
        placeholder="optional note…"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      {fb.isError && <span className="badge bad">failed</span>}
    </div>
  );
}
