# fomc-minutes-follow — ingest the FOMC minutes text, not the release notice

## Requirement

The `press_monetary` RSS item for FOMC minutes points at a short press release ("The Federal
Reserve on Wednesday released the minutes … The minutes can be viewed on the Board's website").
The handler stores that release body, about 800 characters in one chunk, and never fetches the
minutes. So the corpus holds no FOMC minutes text: the staff economic outlook, participants'
views on inflation, growth and the labour market, and the policy discussion.

The config already expects the real text: `section_headings` lists the minutes' sections.
Run on the real minutes page (`fomcminutes20260729.htm`, 2026-10-15), the existing body
selectors extract 39,757 characters, and the existing chunker splits them into 14 chunks across
all six configured sections. The fix is to follow the link.

Effect on retrieval: with no Fed inflation-outlook text, US-inflation questions are answered from
the BoJ outlook report (11–14 of 15–19 chunks). Spec retag-endpoint fixed the tags that excluded
Fed chunks; this spec supplies the most relevant Fed text.

Out of scope:
- the discount-rate minutes (also notice-only; low value);
- the PDF version of the minutes;
- SEP (already followed, for its tables);
- backfilling the stored corpus (an operational step; see Notes).

## Acceptance criteria

- **C1**: `ingest.fed.handlers.press_monetary.minutes_page_url(html, page_url) -> str | None`
  returns the absolute URL, resolved against `page_url`, of the first link whose URL matches
  `/monetarypolicy/fomcminutes\d{8}\.htm` (the HTML minutes page, not the `.pdf`), or `None` when
  there is none. Oracle: hand-written HTML.
- **C2**: `PressMonetaryHandler.fetch` for an item with `doc_type == "fomc_minutes"` fetches the
  release URL, then the C1 minutes URL, and returns one `ParsedDocument` built from the minutes
  page:
  - `url` = the minutes URL; `content_hash` = the minutes page fetch's `content_hash`;
  - `doc_id` = `f"press_monetary_{release_date}_{minutes file name}"`, where `release_date` is
    the date in the **release** URL (e.g. `monetary20260819a.htm` → 2026-08-19);
  - `doc_type` `fomc_minutes`, `publication` `"FOMC Minutes"`, `release_date` as above;
  - the chunk texts contain the minutes article's text and not the release notice's;
  - every configured section heading present in the minutes page is the `section` of at least one
    chunk.

  Oracle: synthetic release and minutes pages served by a fake HTTP client.
- **C3**:
  - **Fallback:** when the release page has no minutes link, or the minutes fetch fails (HTTP
    404), `fetch` returns `[]`. Nothing is stored, so a later run retries; a notice-only document
    is never stored.
  - **Other doc types unchanged:** an `fomc_statement` item makes exactly one request, the
    release URL, and its document's `url` is the release URL.

  Oracle: the fake client's request log.

## Interface

- `ingest.fed.handlers.press_monetary.minutes_page_url(html: bytes | str, page_url: str) -> str |
  None` — new.
- `PressMonetaryHandler.fetch` — existing; follows the link for `fomc_minutes` (C2, C3).

## Notes

- Fixtures: synthetic HTML in the Fed page layout (`div#article`, section headings as their own
  blocks, a `.htm` and a `.pdf` minutes link); a fake HTTP client mapping URL → bytes, recording
  requests; fakes for cache and stores as in the ingest-fred-daily spec. No network, no `data/`,
  no clock.
- **Rollout (operational, after the implementation merges):** the six stored minutes are keyed on
  their release URLs and recorded in the ingest ledger, so a plain re-ingest skips them.
  1. delete the six notice-only `fomc_minutes` files under `tier1/press_monetary/`;
  2. delete their ledger rows (`seen_documents` for those release URLs);
  3. `python -m ingest --tier tier1_fed --endpoints press_monetary --refresh`;
  4. re-index. If the retag rollout has not run yet, combine them: one `index --append`, then
     `index --incremental` to delete the departed notice chunks.
- New documents are keyed on the minutes URL, so their `content_hash` differs from the old
  notices and `index --incremental` sees them as new.
