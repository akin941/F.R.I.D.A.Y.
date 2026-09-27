# Implementation Master Prompt

> Usage: [context header from planning-master.md] + the MVP `brief.md` + the
> approved plan → give to Claude Code on a feature branch (Codex CLI retired from the
> development workflow, 2026-09-27).

## Task

Implement the approved plan exactly. Rules of engagement:

- Work only on the feature branch named in the plan; never commit to `main`.
- One logical step per commit, Conventional Commits format.
- Create only the files listed in the plan inventory; if something extra is
  genuinely needed, say so explicitly in your report instead of silently adding it.
- Write the tests named in the plan alongside the code; run `make test` and
  `make lint` before declaring any step done.
- Never hardcode secrets; new config goes through env + `.env.example`.
- Respect the non-negotiable rules in the context header; when in doubt, leave a
  TODO and flag it rather than guessing.

## Report format

Files created/changed · commands run and their results · deviations from the plan
(with reasons) · open TODOs · what the reviewer should look at first.
