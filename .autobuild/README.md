# .autobuild

Project configuration for **Autobuild**, the planner → implementer → reviewer
control plane in the AI engineering harness (`~/ai-engineering-harness/autobuild`,
also linked as `~/.agents/autobuild`). Autobuild itself is not copied here.

- `config.yaml`: this project's providers, branches, paths, limits, validation, notification and control settings. It's the only Autobuild file this repository owns.
- `runs/`: per-run artifacts. Git-ignored except `.gitkeep`. See the harness `autobuild/docs/contracts/ARTIFACT_CONTRACT.md`.

Run from the repository root, using the `autobuild` command the harness installs on `~/.local/bin`:

```bash
autobuild config .                                     # validate this config
autobuild agents .                                     # providers per role
autobuild brief docs/roadmap/<brief>.md                # validate a brief
autobuild run docs/roadmap/<brief>.md --project . --dry-run
autobuild run docs/roadmap/<brief>.md --project .
```

Validation runs in Autobuild's sandbox against a copy of the run's source, with
no network and no inherited environment:

- **Dependencies:** `.venv` and `web/node_modules` are mounted read-only from
  this checkout (`validation.runtime_paths`). Keep them current locally with
  `pip install -r requirements.txt` and `cd web && npm ci`.
- **Imports:** tests import the run's `src/` through `pytest.ini`'s `pythonpath`.
- **Postgres tests skip:** they need `DATABASE_URL`, which comes from the local
  `.env` and is deliberately not available to Autobuild validation. Run them
  locally.

The harness `autobuild/README.md` covers setup and troubleshooting. Contracts,
schemas and policy live in the harness and aren't copied here.
