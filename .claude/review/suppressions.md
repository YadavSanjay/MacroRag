# review-core suppressions (MacroRAG)

Findings listed here are not re-raised by `review-core`. One line each:
`- <finding id> — false-positive|accepted-risk|wontfix — <reason> — <yyyy-mm-dd>`

`false-positive` entries feed each lens's measured precision — use it only when the
finding was wrong, not when it was right but not worth fixing.
- change-integrity:.claude/review/profile.md:coding-standard:references-files-absent-on-this-base — false-positive — Review tooling artifact: the pack was taken from the feat/coding-standards checkout, not the reviewed change; those references and files were never part of it — 2026-10-06
- macro-data-integrity:src/ingest/fed/handlers/sloos.py:SloosHandler.fetch:last-update-footer-not-publication-date — accepted-risk — Footer date chosen deliberately: the URL survey month predates publication and would leak into ex-ante backtests; a late-biased date is conservative (no look-ahead). — 2026-10-06
