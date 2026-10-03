# .autobuild

Project configuration for **autobuild**, the planner → implementer → reviewer
control plane in the AI engineering harness (`~/.agents/autobuild`, canonical
at `~/ai-engineering-harness/autobuild`).

- `config.yaml`: this project's branches, paths, limits, validation, notification and control settings. It's the only autobuild file this repository owns.
- `runs/`: per-run artifacts. Git-ignored except `.gitkeep`. See the harness `autobuild/docs/ARTIFACT_CONTRACT.md`.

Validate with `~/.agents/autobuild/bin/autobuild config ~/content-automation`.
Contracts, schemas and policy live in the harness and aren't copied here.
