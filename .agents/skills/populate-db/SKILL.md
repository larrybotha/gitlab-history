---
name: populate-db
description: "Populate the gitlab-history SQLite database by fetching MRs, discussions, and file snapshots from GitLab via glab CLI. Use when the database is empty or stale and needs refreshing."
license: MIT
---

# Populate Database

Fetches data from GitLab via `glab` CLI into the project's SQLite database.

## Prerequisites

- `glab` CLI authenticated: `glab auth login`
- uv installed
- Project dependencies synced: `uv sync`

## Steps

### 1. Fetch merge requests

Fetches MRs where the configured user is assignee or reviewer.

```bash
uv run python -m mr_history.fetch_mrs [--db DB_PATH] [--since YYYY-MM-DD] [--role assignee|reviewer|both] [--repo group/project]
```

- `--db` — SQLite path (default: `mr_history.db`)
- `--since` — fetch MRs created after this date (default: `2025-10-01`)
- `--role` — which role to fetch (default: `both`)
- `--repo` — GitLab repo path (default: auto-detected from git remote or env var)

Idempotent: re-running upserts new/changed records only.

### 2. Fetch discussions with diff positions

Fetches all discussions for each MR, including inline diff comment positions (file path, line numbers, commit SHAs).

```bash
uv run python -m mr_history.fetch_discussions [--db DB_PATH] [--skip-system] [--repo group/project]
```

- `--skip-system` — exclude system-generated notes (label changes, commit pushes, etc.)
- `--repo` — GitLab repo path

Must run after step 1 (needs MRs in DB).

### 3. Fetch file snapshots

For each unique (commit_sha, file_path) from diff comments, fetches the full file content at that commit. Used by the web UI to show code context around inline comments.

```bash
uv run python -m mr_history.fetch_file_snapshots [--db DB_PATH] [--repo group/project]
```

- `--repo` — GitLab repo path

Must run after step 2 (needs diff comment positions in DB).

### 4. Verify

```bash
uv run python -c "
from mr_history.models import get_session, Comment, Discussion, FileSnapshot, MergeRequest, User
s = get_session()
print(f'MRs:        {s.query(MergeRequest).count()}')
print(f'Discussions:{s.query(Discussion).count()}')
print(f'Comments:   {s.query(Comment).count()}')
print(f'  Human:    {s.query(Comment).filter(Comment.system == False).count()}')
print(f'  Diff:     {s.query(Comment).filter(Comment.diff_new_path.isnot(None)).count()}')
print(f'Snapshots:  {s.query(FileSnapshot).count()}')
print(f'Users:      {s.query(User).count()}')
s.close()
"
```

## Configuration

Project and user IDs are resolved automatically:

1. **Env vars** (explicit override): `MR_HISTORY_PROJECT_ID`, `MR_HISTORY_USER_ID`
2. **`--repo` flag**: `--repo hugo-systems/practice-manager-orthodontics` (resolves project ID via glab API)
3. **Git remote auto-detect**: if run from inside a GitLab-cloned repo, derives `group/project` from `origin` remote
4. **User**: derived from `glab auth` (the authenticated glab user)

If none resolve, scripts exit with an error message.

## Typical full refresh

```bash
rm -f mr_history.db
uv run python -m mr_history.fetch_mrs
uv run python -m mr_history.fetch_discussions
uv run python -m mr_history.fetch_file_snapshots
```

## Expected volumes (as of 2025-10 to 2026-04)

| Metric | Count |
|--------|-------|
| MRs | ~623 |
| Discussions | ~7,200 |
| Comments (human) | ~1,000 |
| Diff comments | ~700 |
| File snapshots | ~300 |
| Fetch time (total) | ~15-20 min |
