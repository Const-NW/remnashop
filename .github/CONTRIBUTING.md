# Contributing to remnashop

The code rules live in [AGENTS.md](../AGENTS.md) at the repository root, with details in [docs/architecture/](../docs/architecture/). This file covers the process around a pull request. If you work with an AI agent, it reads `AGENTS.md` (Claude Code through `CLAUDE.md`); you are still the one answering for the PR.

When the rules and the code next to your change disagree, follow the code and mention the mismatch in the PR.

## Before you write code

- **Discuss first** (issue or discussion) anything that touches money, customer consent, legal text, the database schema, or more than one module. A design agreed in a few comments is cheaper than a rewritten PR.
- **One concern per PR.** A feature that needs a new settings field type, a background job, an admin page and bot screens is several PRs, merged in order. Rough ceiling: about 800 changed lines of application code, not counting tests.
- **Branch from `dev`** and target `dev`.

## While you work

- Run the verification commands from `AGENTS.md` before every push. CI runs the same plus migrations and `pytest`. Green CI is necessary, not sufficient: most review findings pass every linter.
- Rebase onto the latest `dev` before asking for review. A migration's revision number must follow the current head.
- Don't commit personal tooling: no editor, AI-tool or local-script entries in `.gitignore`, no stray config files. Use your global gitignore.
- Don't hand-write rules for your agent into the repository (`.cursor/rules`, `copilot-instructions.md`, a second `CLAUDE.md`). If a rule is missing from `AGENTS.md`, propose it there.

## The pull request

- Fill in the template. Its checklist mirrors the rules a review checks first.
- **Keep the description true.** Update it on every push that changes scope; a description that names a migration, endpoint or behavior that no longer exists is treated as a bug.
- For a feature behind a flag, list what changes for shops that don't enable it. "Nothing" is a fine answer, but write it.
- For payment code, include the lifecycle table described in `AGENTS.md` ("Money").
- Commit messages in English: `type(scope): subject`, body as past-tense `- ` bullets.
