#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy"]
# ///
"""
Fetch all discussions (with diff positions) for MRs in the database.
Replaces comments from the notes API with richer discussion-based data.

Usage:
    uv run fetch_discussions.py [--db mr_history.db] [--skip-system]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime

from mr_history.config import resolve_project_id
from mr_history.models import Comment, Discussion, MergeRequest, User, get_session

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


def extract_diff_fields(note_data: dict) -> dict:
    """Extract diff position fields from a note."""
    pos = note_data.get("position") or {}
    result = {
        "diff_old_path": pos.get("old_path"),
        "diff_new_path": pos.get("new_path"),
        "diff_old_line": pos.get("old_line"),
        "diff_new_line": pos.get("new_line"),
        "diff_position_type": pos.get("position_type"),
        "diff_base_sha": pos.get("base_sha"),
        "diff_head_sha": pos.get("head_sha"),
        "diff_commit_id": note_data.get("commit_id"),
        "diff_line_range_start": None,
        "diff_line_range_end": None,
        "diff_line_range_type": None,
    }

    line_range = pos.get("line_range") or {}
    start = line_range.get("start") or {}
    end = line_range.get("end") or {}

    if start.get("new_line") is not None:
        result["diff_line_range_start"] = start["new_line"]
        result["diff_line_range_type"] = start.get("type", "new")
    elif start.get("old_line") is not None:
        result["diff_line_range_start"] = start["old_line"]
        result["diff_line_range_type"] = start.get("type", "old")

    if end.get("new_line") is not None:
        result["diff_line_range_end"] = end["new_line"]
    elif end.get("old_line") is not None:
        result["diff_line_range_end"] = end["old_line"]

    return result


def upsert_discussion(session, disc_data: dict, mr_id: int) -> Discussion:
    disc = session.get(Discussion, disc_data["id"])
    if disc is None:
        disc = Discussion(
            id=disc_data["id"],
            merge_request_id=mr_id,
            individual_note=disc_data.get("individual_note", True),
        )
        session.add(disc)
    else:
        disc.individual_note = disc_data.get("individual_note", True)
    return disc


def upsert_comment(
    session, note_data: dict, mr_id: int, discussion_id: str
) -> Comment:
    comment = session.get(Comment, note_data["id"])
    author = upsert_user(session, note_data["author"])

    diff_fields = {}
    if note_data.get("type") == "DiffNote" and note_data.get("position"):
        diff_fields = extract_diff_fields(note_data)

    if comment is None:
        comment = Comment(
            id=note_data["id"],
            merge_request_id=mr_id,
            discussion_id=discussion_id,
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
            **diff_fields,
        )
        session.add(comment)
    else:
        comment.discussion_id = discussion_id
        comment.body = note_data["body"]
        comment.updated_at = parse_dt(note_data.get("updated_at"))
        comment.system = note_data.get("system", False)
        comment.resolvable = note_data.get("resolvable", False)
        comment.resolved = note_data.get("resolved", False)
        comment.type = note_data.get("type")
        for k, v in diff_fields.items():
            setattr(comment, k, v)

    return comment


def fetch_discussions_for_mr(mr_iid: int, project_id: int) -> list[dict]:
    """Fetch all discussions for a merge request, handling pagination."""
    all_discussions = []
    page = 1

    while True:
        endpoint = (
            f"projects/{project_id}/merge_requests/{mr_iid}/discussions"
            f"?per_page={PER_PAGE}"
            f"&page={page}"
        )
        data, headers = glab_api(endpoint)

        if not data:
            break

        all_discussions.extend(data)

        next_page = headers.get("x-next-page", "")
        if not next_page:
            break
        page = int(next_page)

    return all_discussions


def main():
    parser = argparse.ArgumentParser(
        description="Fetch GitLab MR discussions to SQLite"
    )
    parser.add_argument("--db", default="mr_history.db", help="SQLite database path")
    parser.add_argument(
        "--skip-system",
        action="store_true",
        help="Skip system-generated notes",
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
    print(f"Found {len(mrs)} MRs in database. Fetching discussions...")

    total_discussions = 0
    total_comments = 0
    total_diff_notes = 0

    for i, mr in enumerate(mrs, 1):
        print(
            f"  [{i}/{len(mrs)}] !{mr.iid}: {mr.title[:50]}...",
            end=" ",
            flush=True,
        )

        discussions = fetch_discussions_for_mr(mr.iid, project_id)
        disc_count = 0
        note_count = 0
        diff_count = 0

        for disc_data in discussions:
            notes = disc_data.get("notes", [])

            if args.skip_system:
                notes = [n for n in notes if not n.get("system", False)]
                if not notes:
                    continue

            disc = upsert_discussion(session, disc_data, mr.id)
            disc_count += 1

            for note_data in notes:
                upsert_comment(session, note_data, mr.id, disc.id)
                note_count += 1
                if note_data.get("type") == "DiffNote" and note_data.get("position"):
                    diff_count += 1

        total_discussions += disc_count
        total_comments += note_count
        total_diff_notes += diff_count
        print(f"{disc_count} discussions, {note_count} notes ({diff_count} diff)")

        if i % 10 == 0:
            session.commit()

    session.commit()

    # Summary
    comment_count = session.query(Comment).count()
    diff_comment_count = (
        session.query(Comment)
        .filter(Comment.diff_new_path.isnot(None))
        .count()
    )
    discussion_count = session.query(Discussion).count()
    human_count = (
        session.query(Comment).filter(Comment.system == False).count()
    )
    system_count = (
        session.query(Comment).filter(Comment.system == True).count()
    )
    user_count = session.query(User).count()

    print(f"\nDone! Database: {args.db}")
    print(f"  Discussions:    {discussion_count}")
    print(f"  Total comments: {comment_count}")
    print(f"  Human comments: {human_count}")
    print(f"  System notes:   {system_count}")
    print(f"  Diff comments:  {diff_comment_count}")
    print(f"  Users:          {user_count}")

    session.close()


if __name__ == "__main__":
    main()
