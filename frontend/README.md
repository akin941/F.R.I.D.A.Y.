# Jarvis OS — Frontend

Next.js (App Router) web shell for Jarvis OS: auth, workspace switcher, streaming
Chat, and a Today placeholder. Talks only to the Django backend's `/api/v1/*`
endpoints through this app's own Route Handlers (a BFF layer) — the browser never
sees a JWT or calls Django directly.

## Development

```bash
cp .env.example .env.local   # BACKEND_API_URL, APP_ORIGIN
npm ci
npm run dev                  # http://localhost:3001
```

Requires the backend stack running (`make up` from the repo root) with the
`AI_FAKE_PROVIDER=true` dev default, so chat responses are deterministic without a
real LLM key.

## Commands

```bash
npm run dev              # dev server on :3001
npm run build             # production build
npm run lint               # eslint
npm run typecheck          # tsc --noEmit
npm test                   # vitest (mocked, no real backend/network)
npm run test:e2e:install   # one-time: downloads the Chromium browser
npm run test:e2e           # Playwright smoke suite (real backend required, see below)
```

## e2e smoke test

`e2e/smoke.e2e.ts` drives a real browser against a real backend — no mocks —
covering dev signup, the auto-created personal workspace, a full chat send/
stream against the deterministic FakeAdapter, the Today placeholder, logout,
and the route guard. It needs the backend actually running and migrated/seeded
(`make up` from the repo root does all of that); `e2e/global-setup.ts` polls
`/healthz` first and fails with a clear message if the backend isn't up
instead of a confusing test timeout. Playwright's `webServer` starts (or
reuses) the frontend dev server itself — it doesn't need to already be
running. This suite intentionally covers one golden path only; the edge cases
(retry/refresh/CSRF/staleness/races) live in the Vitest suite above, which
mocks the network so it can exercise them cheaply and deterministically.

## Architecture notes

- **BFF, not a direct client**: `src/app/api/**/route.ts` proxy to Django,
  translating JWTs into `HttpOnly` cookies (`jarvis_access`, `jarvis_refresh`,
  `jarvis_session`) the browser can never read.
- **`src/proxy.ts`** (Next 16 renamed `middleware.ts`) is an optimistic route guard
  only — every Route Handler re-checks the access-token cookie independently.
- **`src/lib/sse.ts`** parses the chat SSE stream (`chunk`/`done`/`error` events)
  incrementally, tolerant of chunk/frame boundaries splitting mid-event or
  mid-UTF-8-character.
- **`src/contexts/workspace-context.tsx`** holds the active workspace, defaulting
  to the personal workspace and namespacing the saved preference by user id.
