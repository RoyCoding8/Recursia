# Frontend

Next.js + TypeScript UI for run submission, live graph updates, metrics visibility, and intervention actions.

## Quick start

```powershell
cd "D:/AI/Recursia/frontend"
npm install
npm run dev
```

## Environment setup

Copy the template once:

```powershell
cd "D:/AI/Recursia/frontend"
Copy-Item .env.local.example .env.local
```

Default local value:

- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`

Important:

- This file is for local placeholders only.
- Do not commit real secrets in env files.
- Provider selection happens in the backend `.env` (`LLM_PROVIDER`).

## Test and build commands

```powershell
cd "D:/AI/Recursia/frontend"
npm run typecheck
npm run test
npm run test:components
npm run test:e2e
npm run build
```

## Notes for CI/local runs

- `test:e2e` uses Playwright (`playwright.config.ts`) and requires Playwright browser dependencies to be installed.
- E2E specs under `tests/e2e` mock network/SSE behavior for deterministic UI validation.
- Ensure backend API base URL/env settings match your local setup when running against a live backend.

## Known constraints

- Current dependency set includes `next@14.2.29`; QA reports flagged a high-severity advisory with patch update available.
- Streaming reconnect state is validated in UI tests, but production resilience still depends on backend SSE availability.
