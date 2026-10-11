# Agent instructions

Instructions for AI coding agents (Claude Code and others) working in this repository.

## Git

- Commits and pushes must **not** be attributed to an AI agent: no `Co-Authored-By: Claude …` trailers, no "Generated with Claude Code" lines in commit messages or pull request descriptions. This is a policy requirement.
- Commit as the repository's configured git user only.

## Sources of truth

- [SPEC.md](SPEC.md) is normative. Deviations need an ADR in `docs/adr/` and a SPEC.md update in the same PR.
- [BACKLOG.md](BACKLOG.md) is the committed task breakdown; `backlog/` is a git-ignored local working copy (one file per task). Update a task's **Status** field as work progresses.

## Documentation

- Keep [design-docs/](design-docs/) current as parts are planned and implemented: update the relevant doc's status and its **Implementation notes** (backlog tasks, code locations, decisions, deviations), and add Mermaid diagrams where they help.
