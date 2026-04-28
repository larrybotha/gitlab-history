#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["sqlalchemy"]
# ///
"""
Fetch all merge requests assigned to or reviewed by current user.
Writes results to SQLite via SQLAlchemy models.

Usage:
    uv run fetch_mrs.py [--db mr_history.db] [--since 2025-10-01] [--role both]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime

from sqlalchemy import func

from mr_history.config import resolve_project_id, resolve_user_id
from mr_history.models import MergeRequest, User, get_session

PER_PAGE = 100


def _get_project_and_user() -> tuple[int, int]:
    """Resolve project and user IDs, memoized per run."""
    return resolve_project_id(), resolve_user_id()


PROJECT_ID: int | None = None
USER_ID: int | None = None


def glab_api(endpoint: str) -> tuple[list | dict, dict]:
    """
    Call glab api with --include to get headers + body.
    Returns (parsed_json, headers_dict).
    """
    cmd = ["glab", "api", endpoint, "--include"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR: glab api failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

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
        user.avatar_url = user_data.get("avatar_url")
        user.web_url = user_data.get("web_url")
    return user


def _merge_role(existing: str | None, new_role: str) -> str:
    """Combine roles: upgrade to 'both' if MR found via both queries."""
    if not existing or existing == new_role:
        return new_role
    return "both"


def upsert_mr(session, mr_data: dict, role: str) -> MergeRequest:
    """Insert or update a merge request record."""
    mr = session.get(MergeRequest, mr_data["id"])

    author = upsert_user(session, mr_data["author"])

    merged_by_id = None
    if mr_data.get("merged_by"):
        merged_by_user = upsert_user(session, mr_data["merged_by"])
        merged_by_id = merged_by_user.id
    elif mr_data.get("merge_user"):
        merged_by_user = upsert_user(session, mr_data["merge_user"])
        merged_by_id = merged_by_user.id

    labels = ",".join(mr_data.get("labels", []))

    if mr is None:
        mr = MergeRequest(
            id=mr_data["id"],
            iid=mr_data["iid"],
            project_id=mr_data["project_id"],
            title=mr_data["title"],
            description=mr_data.get("description"),
            state=mr_data["state"],
            source_branch=mr_data.get("source_branch"),
            target_branch=mr_data.get("target_branch"),
            created_at=parse_dt(mr_data["created_at"]),
            updated_at=parse_dt(mr_data.get("updated_at")),
            merged_at=parse_dt(mr_data.get("merged_at")),
            closed_at=parse_dt(mr_data.get("closed_at")),
            user_notes_count=mr_data.get("user_notes_count", 0),
            upvotes=mr_data.get("upvotes", 0),
            downvotes=mr_data.get("downvotes", 0),
            web_url=mr_data.get("web_url"),
            labels=labels,
            has_conflicts=mr_data.get("has_conflicts"),
            draft=mr_data.get("draft"),
            author_id=author.id,
            merged_by_id=merged_by_id,
            user_role=role,
        )
        session.add(mr)
    else:
        mr.title = mr_data["title"]
        mr.description = mr_data.get("description")
        mr.state = mr_data["state"]
        mr.source_branch = mr_data.get("source_branch")
        mr.target_branch = mr_data.get("target_branch")
        mr.updated_at = parse_dt(mr_data.get("updated_at"))
        mr.merged_at = parse_dt(mr_data.get("merged_at"))
        mr.closed_at = parse_dt(mr_data.get("closed_at"))
        mr.user_notes_count = mr_data.get("user_notes_count", 0)
        mr.upvotes = mr_data.get("upvotes", 0)
        mr.downvotes = mr_data.get("downvotes", 0)
        mr.web_url = mr_data.get("web_url")
        mr.labels = labels
        mr.has_conflicts = mr_data.get("has_conflicts")
        mr.draft = mr_data.get("draft")
        mr.merged_by_id = merged_by_id
        mr.user_role = _merge_role(mr.user_role, role)

    return mr


def fetch_all_mrs(since: str, role: str, project_id: int, user_id: int) -> list[dict]:
    """
    Fetch all MRs for user since given date, handling pagination.

    Parameters
    ----------
    since : str
        Date string (YYYY-MM-DD).
    role : str
        'assignee' or 'reviewer' — determines API filter param.
    project_id : int
        GitLab project ID.
    user_id : int
        GitLab user ID.
    """
    all_mrs = []
    page = 1

    filter_param = "assignee_id" if role == "assignee" else "reviewer_id"

    while True:
        endpoint = (
            f"projects/{project_id}/merge_requests"
            f"?{filter_param}={user_id}"
            f"&created_after={since}T00:00:00Z"
            f"&per_page={PER_PAGE}"
            f"&page={page}"
            f"&state=all"
            f"&order_by=created_at"
            f"&sort=asc"
        )
        print(f"  Fetching page {page}...", end=" ", flush=True)
        data, headers = glab_api(endpoint)
        print(f"got {len(data)} MRs")

        if not data:
            break

        all_mrs.extend(data)

        next_page = headers.get("x-next-page", "")
        if not next_page:
            break
        page = int(next_page)

    return all_mrs


def main():
    parser = argparse.ArgumentParser(description="Fetch GitLab MRs to SQLite")
    parser.add_argument("--db", default="mr_history.db", help="SQLite database path")
    parser.add_argument(
        "--since", default="2025-10-01", help="Fetch MRs created after this date"
    )
    parser.add_argument(
        "--role",
        choices=["assignee", "reviewer", "both"],
        default="both",
        help="Fetch MRs where user is assignee, reviewer, or both (default: both)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="GitLab repo path (group/project). Auto-detected from git remote if omitted.",
    )
    args = parser.parse_args()

    project_id = resolve_project_id(args.repo)
    user_id = resolve_user_id()

    session = get_session(args.db)

    roles = (
        ["assignee", "reviewer"] if args.role == "both" else [args.role]
    )

    for role in roles:
        print(f"\nFetching MRs as {role} for user {user_id} since {args.since}...")
        mrs_data = fetch_all_mrs(args.since, role, project_id, user_id)
        print(f"Total MRs fetched as {role}: {len(mrs_data)}")

        print("Writing to database...")
        for i, mr_data in enumerate(mrs_data, 1):
            upsert_mr(session, mr_data, role)
            if i % 50 == 0:
                print(f"  Processed {i}/{len(mrs_data)} MRs")
                session.commit()

        session.commit()

    # Summary
    mr_count = session.query(MergeRequest).count()
    user_count = session.query(User).count()
    role_counts = dict(
        session.query(MergeRequest.user_role, func.count(MergeRequest.id))
        .group_by(MergeRequest.user_role)
        .all()
    )
    print(f"\nDone! Database: {args.db}")
    print(f"  Total MRs: {mr_count}")
    print(f"  Users: {user_count}")
    print(f"  Role breakdown: {role_counts}")

    session.close()


if __name__ == "__main__":
    main()
