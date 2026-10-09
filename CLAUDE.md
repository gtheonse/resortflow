# ResortFlow — Claude Code notes

@AGENTS.md

The project guide, architectural commitments, hard rules, commands, and module ownership are in
`AGENTS.md` (imported above). This file adds only what is specific to Claude Code.

## Working in this repo with Claude Code

- **Do not load all of `docs/`.** It is large. Use the documentation map in `AGENTS.md` and read
  only the document the current task needs. `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` is the
  starting point.
- **Spec-driven workflow.** The `/speckit-*` skills in `.claude/skills/` drive feature work:
  `specify → clarify → plan → tasks → analyze → implement → converge`. Do not implement outside
  `/speckit-implement`. If you skip a step, record why in an ADR.
- **Run tests in the container.** `make test-docker` is the supported route on Windows hosts,
  where Smart App Control can block unsigned native wheels.
- **Contracts and node names are frozen.** Raise a contract change as an issue instead of editing
  `docs/contracts/` unilaterally, and never rename a graph node in a patch release.
- **Secrets.** Never open `.env`. Use `.env.example` for variable names.
