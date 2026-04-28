#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy"]
# ///
"""
Fetch all comments (notes) for merge requests already in the database.
Writes results to SQLite via SQLAlchemy models.

Usage:
    uv run fetch_comments.py [--db mr_history.db] [--skip-system]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime

from mr_history.config import resolve_project_id
from mr_history.models import Comment, MergeRequest, User, get_session

PER_PAGE = 100


def glab_api(endpoint: str) -> tuple[list | dict, dict]:
    """Call glab api with --include. Returns (parsed_json, headers)."""
    cmd = ["glab", "api", endpoint, "--include"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: glab api failed: {result.stderr}", file=sys.stderr)
        return [], {}

    output = result.stdout
    parts = output.split("\n\n", 1)
    if len(parts) == 2:
        header_block, body = parts
    else:
        header_block = ""
        body = output

    headers = {}
    for line in header_block.strip().splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            headers[key.strip().lower()] = val.strip()

    data = json.loads(body)
    return data, headers


def parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


def upsert_user(session, user_data: dict) -> User:
    """Insert or update a user record."""
    user = session.get(User, user_data["id"])
    if user is None:
        user = User(
            id=user_data["id"],
            username=user_data["username"],
            name=user_data["name"],
            state=user_data["state"],
            avatar_url=user_data.get("avatar_url"),
            web_url=user_data.get("web_url"),
        )
        session.add(user)
    else:
        user.username = user_data["username"]
        user.name = user_data["name"]
        user.state = user_data["state"]
    return user


def upsert_comment(session, note_data: dict, mr_id: int) -> Comment:
    """Insert or update a comment record."""
    comment = session.get(Comment, note_data["id"])

    author = upsert_user(session, note_data["author"])

    if comment is None:
        comment = Comment(
            id=note_data["id"],
            merge_request_id=mr_id,
            author_id=author.id,
            body=note_data["body"],
            created_at=parse_dt(note_data["created_at"]),
            updated_at=parse_dt(note_data.get("updated_at")),
            system=note_data.get("system", False),
            resolvable=note_data.get("resolvable", False),
            resolved=note_data.get("resolved", False),
            noteable_type=note_data.get("noteable_type"),
            type=note_data.get("type"),
            confidential=note_data.get("confidential", False),
        )
        session.add(comment)
    else:
        comment.body = note_data["body"]
        comment.updated_at = parse_dt(note_data.get("updated_at"))
        comment.system = note_data.get("system", False)
        comment.resolvable = note_data.get("resolvable", False)
        comment.resolved = note_data.get("resolved", False)
        comment.type = note_data.get("type")

    return comment


def fetch_notes_for_mr(mr_iid: int, project_id: int) -> list[dict]:
    """Fetch all notes for a merge request, handling pagination."""
    all_notes = []
    page = 1

    while True:
        endpoint = (
            f"projects/{project_id}/merge_requests/{mr_iid}/notes"
            f"?per_page={PER_PAGE}"
            f"&page={page}"
            f"&order_by=created_at"
            f"&sort=asc"
        )
        data, headers = glab_api(endpoint)

        if not data:
            break

        all_notes.extend(data)

        next_page = headers.get("x-next-page", "")
        if not next_page:
            break
        page = int(next_page)

    return all_notes


def main():
    parser = argparse.ArgumentParser(
        description="Fetch GitLab MR comments to SQLite"
    )
    parser.add_argument("--db", default="mr_history.db", help="SQLite database path")
    parser.add_argument(
        "--skip-system",
        action="store_true",
        help="Skip system-generated notes (commits added, labels changed, etc.)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitLab repo path (group/project). Auto-detected from git remote if omitted.",
    )
    args = parser.parse_args()

    project_id = resolve_project_id(args.repo)
    session = get_session(args.db)

    mrs = session.query(MergeRequest).order_by(MergeRequest.iid.asc()).all()
    print(f"Found {len(mrs)} MRs in database. Fetching comments...")

    total_comments = 0
    for i, mr in enumerate(mrs, 1):
        print(f"  [{i}/{len(mrs)}] !{mr.iid}: {mr.title[:50]}...", end=" ", flush=True)

        notes = fetch_notes_for_mr(mr.iid, project_id)

        count = 0
        for note in notes:
            if args.skip_system and note.get("system", False):
                continue
            upsert_comment(session, note, mr.id)
            count += 1

        total_comments += count
        print(f"{count} comments")

        if i % 10 == 0:
            session.commit()

    session.commit()

    # Summary
    comment_count = session.query(Comment).count()
    system_count = session.query(Comment).filter(Comment.system == True).count()
    human_count = session.query(Comment).filter(Comment.system == False).count()
    user_count = session.query(User).count()

    print(f"\nDone! Database: {args.db}")
    print(f"  Total comments: {comment_count}")
    print(f"  Human comments: {human_count}")
    print(f"  System notes:   {system_count}")
    print(f"  Users: {user_count}")

    session.close()


if __name__ == "__main__":
    main()
