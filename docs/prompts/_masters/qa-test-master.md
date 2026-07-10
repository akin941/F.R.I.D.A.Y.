# QA / Test Master Prompt

> Usage: [context header from planning-master.md] + the MVP `brief.md` → run after
> implementation review. Writes/deepens tests only; does not change feature code.

## Task

1. Build an acceptance-criterion ↔ test mapping table; any uncovered criterion
   becomes a new test.
2. Permission matrix tests: role × endpoint; at least one "unauthorized → 403/404"
   test per endpoint.
3. Edge cases: empty input, oversized input, unicode, concurrency, idempotency.
4. Failure modes with the mock LLM provider: timeout, invalid/schema-breaking
   output, rate limit — the system must degrade loudly, not silently.
5. Cross-workspace isolation attempts for every new surface (API, cache, retrieval).
6. E2E happy path for the MVP's core flow.

Rules: tests that hit a real LLM are nightly-marked only; everything else runs on
every commit. Finish by running `make test` and reporting coverage of the new code.
