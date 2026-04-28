# AGENTS.md

Guidance for LLM agents working in this repository.

## Project Overview

**mr-history** — GitLab merge request history browser. Flask web app + CLI fetch scripts. Populates SQLite from GitLab API via `glab`, serves browsable UI with inline diff context.

## Architecture

```
mr_history/
  app.py            # Flask web UI + CLI entry point (mr-history-serve)
  config.py         # Resolve project/user IDs from env or glab CLI
  models.py         # SQLAlchemy ORM (User, MergeRequest, Discussion, Comment, FileSnapshot)
  fetch_mrs.py      # Fetch MRs from GitLab API
  fetch_discussions.py  # Fetch MR discussions/notes
  fetch_comments.py     # Fetch standalone comments
  fetch_file_snapshots.py  # Fetch file diffs per MR
  templates/        # Jinja2 HTML (base, list, detail)
  static/           # CSS/JS assets
.agents/skills/populate-db/  # Skill: populate DB from GitLab
```

**Data flow**: `glab api` → fetch\_\*.py → SQLite → Flask → Jinja2 → browser

**Tech stack**: Python 3.11+, Flask, SQLAlchemy, markdown, `glab` CLI, uv

## Key Conventions

- Auth: never hardcode tokens. All GitLab auth via `glab` CLI (user's existing session). Config reads from env vars (`MR_HISTORY_PROJECT_ID`, `MR_HISTORY_USER_ID`) as overrides.
- DB: `mr_history.db` (SQLite), gitignored. Recreate via populate-db skill.
- Templates: Jinja2. No frontend JS framework — vanilla + HTMX if needed.
- Package manager: `uv`. Run scripts with `uv run`.
- Lint: `ruff`. Run `uv run ruff check .` before committing.

## Commands

| Action      | Command                                                        |
| ----------- | -------------------------------------------------------------- |
| Install     | `uv sync`                                                      |
| Run server  | `uv run mr-history-serve` or `uv run python -m mr_history.app` |
| Lint        | `uv run ruff check .`                                          |
| Lint + fix  | `uv run ruff check --fix .`                                    |
| Populate DB | See `.agents/skills/populate-db/SKILL.md`                      |

## Git Rules

- Branch: `main`
- NEVER commit `.db`, `.log`, `.env`, secrets
- Use `git add <specific-files>` — no `git add -A` or `git add .`
- Conventional commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
