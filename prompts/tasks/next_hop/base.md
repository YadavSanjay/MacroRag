You are planning MULTI-HOP retrieval: answering a macro question by walking its
causal chain one link at a time. Given the ORIGINAL question, the SUB-QUESTIONS
already retrieved, and the EVIDENCE gathered so far, decide the SINGLE next
sub-question that follows causally from the evidence and moves toward answering
the original — or stop if the chain is already sufficiently covered.

Rules:
- The next sub-question must be a NEW link, not a repeat of an earlier one.
- Follow the causal chain: shock -> transmission channel -> affected factor
  (e.g. Taiwan conflict -> semiconductor supply disruption -> inflation ->
  monetary-policy reaction -> rates).
- Make it concrete and retrievable, not broad.
- Set done=true when the gathered evidence already spans the chain needed to
  answer the ORIGINAL question.

Return STRICT JSON:
  {"done": <true|false>, "next_query": "<one sub-question; empty if done>",
   "reason": "<= 1 short sentence"}

ORIGINAL:
{question}

SUB-QUESTIONS SO FAR:
{hops}

EVIDENCE SO FAR:
{evidence}

Return ONLY the JSON object.
