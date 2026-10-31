// Minimal SSE-over-POST reader. EventSource only supports GET, but the backend's
// /query/stream is a POST endpoint that emits `event:`/`data:` frames, so we parse
// the stream from a fetch ReadableStream ourselves.

export interface SseFrame {
  event: string;
  data: string;
}

export interface PostSseOptions {
  path: string;
  body: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onFrame: (frame: SseFrame) => void;
}

export async function postSse({
  path,
  body,
  headers = {},
  signal,
  onFrame,
}: PostSseOptions): Promise<void> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...headers },
    body: JSON.stringify(body),
    signal,
  });
  await readSse(res, onFrame);
}

export interface GetSseOptions {
  path: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  onFrame: (frame: SseFrame) => void;
}

// GET-based SSE reader (fetch, not EventSource, so an X-API-Key header can be set).
// Used to follow a background job's log stream (/admin/jobs/{id}/stream).
export async function getSse({
  path,
  headers = {},
  signal,
  onFrame,
}: GetSseOptions): Promise<void> {
  const res = await fetch(path, {
    headers: { Accept: "text/event-stream", ...headers },
    signal,
  });
  await readSse(res, onFrame);
}

async function readSse(res: Response, onFrame: (f: SseFrame) => void): Promise<void> {
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      if (j?.detail) detail = j.detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      onFrame(parseFrame(raw));
    }
  }
}

function parseFrame(raw: string): SseFrame {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  return { event, data: dataLines.join("\n") };
}
