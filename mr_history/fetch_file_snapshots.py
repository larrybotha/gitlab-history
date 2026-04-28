#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy"]
# ///
"""
Fetch file content at specific commits for all diff comments in the database.
Stores file snapshots for showing code context alongside inline comments.

Usage:
    uv run fetch_file_snapshots.py [--db mr_history.db]
"""

import argparse
import base64
import json
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy import func, text

from mr_history.config import resolve_project_id
from mr_history.models import Comment, FileSnapshot, MergeRequest, get_session


def glab_api(endpoint: str) -> tuple[dict | list | None, dict]:
    """Call glab api. Returns (parsed_json, headers)."""
    cmd = ["glab", "api", endpoint]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return None, {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, {}

    return data, {}


def fetch_file_content(
    commit_sha: str, file_path: str, project_id: int
) -> str | None:
    """Fetch file content from GitLab at a specific commit."""
    encoded_path = file_path.replace("/", "%2F").replace(".", "%2E")
    endpoint = (
        f"projects/{project_id}/repository/files/{encoded_path}"
        f"?ref={commit_sha}"
    )
    data, _ = glab_api(endpoint)

    if not data or "content" not in data:
        return None

    encoding = data.get("encoding", "base64")
    if encoding == "base64":
        try:
            return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        except Exception:
            return None
    return data["content"]


def main():
    parser = argparse.ArgumentParser(
        description="Fetch file snapshots for diff comments"
    )
    parser.add_argument("--db", default="mr_history.db", help="SQLite database path")
    parser.add_argument(
        "--repo",
        default=None,
        help="GitLab repo path (group/project). Auto-detected from git remote if omitted.",
    )
    args = parser.parse_args()

    project_id = resolve_project_id(args.repo)
    session = get_session(args.db)

    # Collect unique (sha, path) pairs from diff comments
    result = session.execute(text("""
        SELECT diff_head_sha AS sha, diff_new_path AS path FROM comments
        WHERE diff_new_path IS NOT NULL
          AND diff_head_sha IS NOT NULL
          AND system = 0
        UNION
        SELECT diff_base_sha AS sha, diff_old_path AS path FROM comments
        WHERE diff_old_line IS NOT NULL
          AND diff_new_line IS NULL
          AND diff_base_sha IS NOT NULL
          AND diff_old_path IS NOT NULL
          AND system = 0
    """))
    pairs = [(row.sha, row.path) for row in result]

    # Check which ones we already have
    existing = set()
    for snap in session.query(FileSnapshot).all():
        existing.add((snap.commit_sha, snap.file_path))

    to_fetch = [(sha, path) for sha, path in pairs if (sha, path) not in existing]

    print(f"Unique file snapshots needed: {len(pairs)}")
    print(f"Already in database: {len(existing)}")
    print(f"To fetch: {len(to_fetch)}")

    if not to_fetch:
        print("Nothing to fetch.")
        session.close()
        return

    fetched = 0
    failed = 0

    for i, (sha, path) in enumerate(to_fetch, 1):
        print(f"  [{i}/{len(to_fetch)}] {sha[:12]}:{path}", end=" ", flush=True)

        content = fetch_file_content(sha, path, project_id)

        if content is None:
            print("FAILED (file not found or commit missing)")
            failed += 1
            continue

        line_count = len(content.split("\n"))
        now = datetime.now(timezone.utc)

        snap = FileSnapshot(
            commit_sha=sha,
            file_path=path,
            content=content,
            line_count=line_count,
            fetched_at=now,
        )
        session.add(snap)
        fetched += 1
        print(f"OK ({line_count} lines)")

        if i % 20 == 0:
            session.commit()

    session.commit()

    # Summary
    total_snaps = session.query(FileSnapshot).count()
    print(f"\nDone! Database: {args.db}")
    print(f"  Fetched:  {fetched}")
    print(f"  Failed:   {failed}")
    print(f"  Total snapshots in DB: {total_snaps}")

    session.close()


if __name__ == "__main__":
    main()
