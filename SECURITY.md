# Security policy

MacroRAG is a personal project maintained on a best-effort basis.

## Reporting a vulnerability

Please report a vulnerability privately through GitHub's **Report a vulnerability** button on
the repository's **Security** tab (private vulnerability reporting), not in a public issue.
Include the affected file or endpoint, steps to reproduce, and the impact you expect.

There is no fixed response time. Only the latest commit on `main` gets fixes.

## Scope notes

- The REST API's API keys identify callers for rate limiting and cost attribution; the corpus is
  shared and the API is **not** multi-tenant.
- Never put real credentials in an issue, pull request or commit. Secrets belong in `.env`
  (gitignored) or a `<NAME>_FILE` mount.
