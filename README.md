# Recursia

Recursia is a recursive orchestration system for turning a single objective into a structured execution tree, evaluating intermediate results, and surfacing the full run in a live graph UI.

The project is split into a FastAPI backend and a Next.js frontend. The backend handles run lifecycle, recursive execution, persona routing, checker evaluation, merge flow, and event streaming. The frontend provides Mission Control for starting runs, inspecting node state, reviewing proposed files, and applying approved changes into a user-selected folder.

## Highlights

- Recursive divide-route-execute workflow
- Persona-based execution with markdown-backed persona profiles
- Live run graph and event console
- Proposal-based file generation instead of opaque server-side writes
- Review-and-apply workflow for writing approved files into a selected folder
- Separate validation feedback for checker verdicts versus real runtime failures
- Checker self-heal: configurable `pause` (human-in-the-loop) and `auto_retry` (autonomous retry with feedback injection) modes for checker failures
- Scrollable detail panels and batch apply with visual feedback

## Repository Layout

- `backend/` FastAPI API, orchestration runtime, state management, and tests
- `frontend/` Next.js Mission Control UI and component/E2E tests
- `personas/` markdown persona profiles discovered at runtime
- `theory/` product and architecture notes
- `run.bat` Windows launcher for local setup and development

## Quick Start

### Launchers

Windows:

```powershell
.\run.bat
```

Linux and macOS:

```bash
bash ./run.sh
```

Both launchers can install dependencies, start the backend and frontend, and open the app in your browser when the environment supports it.

### Manual startup

Backend:

```powershell
cd backend
uv sync
uv run uvicorn main:app --reload
```

Frontend:

```powershell
cd frontend
npm ci
npm run dev
```

The frontend expects the backend at `http://127.0.0.1:8000` by default.

## Tooling

The backend is managed as a `uv` project and should be installed with `uv sync`. The frontend uses `npm` with the checked-in `package-lock.json` for deterministic installs.

## Personas

Persona profiles are loaded from `personas/*.md`. The frontend exposes these profiles as selectable base personas when starting a run. The selected base persona is applied to the root node, while downstream nodes may still route to other personas as needed.

## Development Notes

- Backend tests live under `backend/tests/`
- Frontend tests live under `frontend/tests/`
- Local generated artifacts are intentionally excluded from version control via `.gitignore`

## Continuous Integration

GitHub Actions runs basic checks on push and pull request across Windows, macOS, and Linux:

- backend `uv run pytest -q`
- frontend `npm run typecheck`
- frontend `npm run test:components`

## License

This project is released under the Apache License 2.0.
