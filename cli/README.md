# Jarvis OS — CLI

The CLI client landed in MVP 9 at `backend/integrations/cli/jarvis_cli.py`
(a thin client over the same REST API the web UI uses, PAT-authenticated,
with the standard channel restriction — no approvals from this channel).
This directory is kept only for the top-level repository layout; there is
no separate package here. See `docs/integrations/README.md` for setup and
usage (`create_dev_pat` command, `~/.config/jarvis/cli.toml`, invocation).
