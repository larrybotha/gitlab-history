#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["flask", "sqlalchemy", "markdown"]
# ///
"""
Web UI for browsing GitLab MR history.

Usage:
    uv run app.py [--db mr_history.db] [--port 5111]
"""

import argparse
import os
import re
import sys
from datetime import datetime

import markdown
from flask import Flask, render_template, request
from sqlalchemy import func

from mr_history.models import Comment, Discussion, FileSnapshot, MergeRequest, User, get_engine, Base
from sqlalchemy.orm import sessionmaker

app = Flask(__name__, template_folder="templates", static_folder="static")


def get_db_session():
    """Get a scoped session from app config."""
    engine = get_engine(app.config["DB_PATH"])
    Session = sessionmaker(bind=engine)
    return Session()


def format_date(dt):
    """Format datetime for display."""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.strftime("%d %b %Y %H:%M")


def relative_date(dt):
    """Return relative time string."""
    if not dt:
        return ""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    delta = now - dt
    days = delta.days
    if days == 0:
        hours = delta.seconds // 3600
        if hours == 0:
            mins = delta.seconds // 60
            return f"{mins}m ago" if mins > 0 else "just now"
        return f"{hours}h ago"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    return f"{days // 365}y ago"


def md_to_html(text):
    """Convert markdown text to HTML."""
    if not text:
        return ""
    return markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br", "sane_lists"],
    )


app.jinja_env.filters["format_date"] = format_date
app.jinja_env.filters["relative_date"] = relative_date
app.jinja_env.filters["md"] = md_to_html


def get_diff_context(session, comment, context_lines: int = 5) -> dict | None:
    """
    Look up file snapshot and extract code context around a diff comment.
    Returns dict with 'lines' list and 'file_path', or None.
    """
    if not comment.has_diff_position:
        return None

    # Determine which SHA/path to look up
    if comment.diff_new_line is not None:
        sha = comment.diff_head_sha
        path = comment.diff_new_path
        target_line = comment.diff_new_line
        # Use line range if available
        if comment.diff_line_range_start and comment.diff_line_range_end:
            target_line = comment.diff_line_range_start
    elif comment.diff_old_line is not None:
        sha = comment.diff_base_sha
        path = comment.diff_old_path
        target_line = comment.diff_old_line
        if comment.diff_line_range_start and comment.diff_line_range_end:
            target_line = comment.diff_line_range_start
    else:
        return None

    if not sha or not path:
        return None

    snap = session.query(FileSnapshot).filter_by(
        commit_sha=sha, file_path=path
    ).first()

    if not snap or not snap.content:
        return None

    # Determine the end line for the highlight range
    end_line = target_line
    if comment.diff_line_range_end and (
        comment.diff_new_line is not None or comment.diff_old_line is not None
    ):
        end_line = comment.diff_line_range_end
    elif comment.diff_new_line and comment.diff_new_line != target_line:
        end_line = comment.diff_new_line
    elif comment.diff_old_line and comment.diff_old_line != target_line:
        end_line = comment.diff_old_line

    ctx = snap.get_context(
        center_line=target_line,
        context_lines=context_lines,
    )

    if not ctx:
        return None

    # Mark lines in the target range
    for line_info in ctx:
        line_info["in_range"] = target_line <= line_info["line"] <= end_line

    return {
        "lines": ctx,
        "file_path": path,
        "target_start": target_line,
        "target_end": end_line,
    }


@app.route("/")
def mr_list():
    session = get_db_session()

    # Filters
    state_filter = request.args.get("state", "all")
    role_filter = request.args.get("role", "all")
    label_filter = request.args.get("label", "")
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "created_desc")
    page = int(request.args.get("page", 1))
    per_page = 30

    query = session.query(MergeRequest).join(
        User, MergeRequest.author_id == User.id
    )

    if state_filter and state_filter != "all":
        query = query.filter(MergeRequest.state == state_filter)

    if role_filter and role_filter != "all":
        if role_filter == "both":
            query = query.filter(MergeRequest.user_role == "both")
        else:
            query = query.filter(
                (MergeRequest.user_role == role_filter)
                | (MergeRequest.user_role == "both")
            )

    if label_filter:
        # Labels stored comma-separated; use LIKE for substring match
        query = query.filter(MergeRequest.labels.like(f"%{label_filter}%"))

    if search:
        like_term = f"%{search}%"
        query = query.filter(
            (MergeRequest.title.ilike(like_term))
            | (MergeRequest.iid == int(search) if search.isdigit() else False)
            | (MergeRequest.source_branch.ilike(like_term))
        )

    # Sorting
    sort_map = {
        "created_desc": MergeRequest.created_at.desc(),
        "created_asc": MergeRequest.created_at.asc(),
        "updated_desc": MergeRequest.updated_at.desc(),
        "updated_asc": MergeRequest.updated_at.asc(),
        "title_asc": MergeRequest.title.asc(),
        "title_desc": MergeRequest.title.desc(),
        "notes_desc": MergeRequest.user_notes_count.desc(),
    }
    query = query.order_by(sort_map.get(sort, MergeRequest.created_at.desc()))

    total = query.count()
    mrs = query.offset((page - 1) * per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    # Stats for sidebar
    state_counts = dict(
        session.query(MergeRequest.state, func.count(MergeRequest.id))
        .group_by(MergeRequest.state)
        .all()
    )
    state_counts["all"] = sum(state_counts.values())

    # Role counts
    role_counts = dict(
        session.query(MergeRequest.user_role, func.count(MergeRequest.id))
        .group_by(MergeRequest.user_role)
        .all()
    )
    role_counts["all"] = sum(role_counts.values())

    # Unique labels
    all_labels_raw = session.query(MergeRequest.labels).distinct().all()
    label_set = set()
    for (lbl,) in all_labels_raw:
        if lbl:
            for l in lbl.split(","):
                l = l.strip()
                if l:
                    label_set.add(l)
    all_labels = sorted(label_set)

    # Comment counts per MR (for the displayed page)
    mr_ids = [mr.id for mr in mrs]
    human_comment_counts = {}
    if mr_ids:
        counts = (
            session.query(Comment.merge_request_id, func.count(Comment.id))
            .filter(Comment.merge_request_id.in_(mr_ids), Comment.system == False)
            .group_by(Comment.merge_request_id)
            .all()
        )
        human_comment_counts = dict(counts)

    session.close()

    return render_template(
        "list.html",
        mrs=mrs,
        state_filter=state_filter,
        role_filter=role_filter,
        label_filter=label_filter,
        search=search,
        sort=sort,
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        state_counts=state_counts,
        role_counts=role_counts,
        all_labels=all_labels,
        human_comment_counts=human_comment_counts,
    )


@app.route("/mr/<int:mr_iid>")
def mr_detail(mr_iid):
    session = get_db_session()

    mr = session.query(MergeRequest).filter(MergeRequest.iid == mr_iid).first()
    if not mr:
        session.close()
        return "MR not found", 404

    author = session.get(User, mr.author_id)
    merged_by = session.get(User, mr.merged_by_id) if mr.merged_by_id else None

    show_system = request.args.get("system", "0") == "1"
    view_mode = request.args.get("view", "discussions")  # "discussions" or "flat"

    # Fetch all discussions with their comments for this MR
    discussions_query = (
        session.query(Discussion)
        .filter(Discussion.merge_request_id == mr.id)
        .all()
    )

    # Build structured discussion list
    discussion_list = []
    for disc in discussions_query:
        notes_query = (
            session.query(Comment, User)
            .join(User, Comment.author_id == User.id)
            .filter(Comment.discussion_id == disc.id)
            .order_by(Comment.created_at.asc())
        )

        if not show_system:
            notes_query = notes_query.filter(Comment.system == False)

        notes = notes_query.all()
        if notes:
            # Attach diff context to first note if it has a diff position
            first_comment, _ = notes[0]
            diff_ctx = get_diff_context(session, first_comment)

            discussion_list.append({
                "id": disc.id,
                "individual_note": disc.individual_note,
                "notes": notes,
                "first_note": notes[0],
                "has_diff": first_comment.has_diff_position,
                "diff_context": diff_ctx,
            })

    # Sort discussions by first note's created_at
    discussion_list.sort(key=lambda d: d["first_note"][0].created_at)

    # Also provide flat comment list for flat view, with diff context
    flat_query = (
        session.query(Comment, User)
        .join(User, Comment.author_id == User.id)
        .filter(Comment.merge_request_id == mr.id)
        .order_by(Comment.created_at.asc())
    )
    if not show_system:
        flat_query = flat_query.filter(Comment.system == False)
    flat_comments = flat_query.all()

    # Pre-compute diff context for flat view
    flat_diff_contexts = {}
    for comment, _ in flat_comments:
        if comment.has_diff_position:
            ctx = get_diff_context(session, comment)
            if ctx:
                flat_diff_contexts[comment.id] = ctx

    total_comments = (
        session.query(func.count(Comment.id))
        .filter(Comment.merge_request_id == mr.id)
        .scalar()
    )
    human_comments = (
        session.query(func.count(Comment.id))
        .filter(Comment.merge_request_id == mr.id, Comment.system == False)
        .scalar()
    )
    system_comments = total_comments - human_comments
    diff_comments = (
        session.query(func.count(Comment.id))
        .filter(
            Comment.merge_request_id == mr.id,
            Comment.diff_new_path.isnot(None),
            Comment.system == False,
        )
        .scalar()
    )

    # Files with comments (for summary)
    commented_files = (
        session.query(
            Comment.diff_new_path,
            func.count(Comment.id),
        )
        .filter(
            Comment.merge_request_id == mr.id,
            Comment.diff_new_path.isnot(None),
            Comment.system == False,
        )
        .group_by(Comment.diff_new_path)
        .order_by(func.count(Comment.id).desc())
        .all()
    )

    # Prev/next MR navigation
    prev_mr = (
        session.query(MergeRequest)
        .filter(MergeRequest.iid < mr_iid)
        .order_by(MergeRequest.iid.desc())
        .first()
    )
    next_mr = (
        session.query(MergeRequest)
        .filter(MergeRequest.iid > mr_iid)
        .order_by(MergeRequest.iid.asc())
        .first()
    )

    session.close()

    return render_template(
        "detail.html",
        mr=mr,
        author=author,
        merged_by=merged_by,
        user_role=mr.user_role,
        discussions=discussion_list,
        flat_comments=flat_comments,
        view_mode=view_mode,
        show_system=show_system,
        total_comments=total_comments,
        human_comments=human_comments,
        system_comments=system_comments,
        diff_comments=diff_comments,
        commented_files=commented_files,
        flat_diff_contexts=flat_diff_contexts,
        prev_mr=prev_mr,
        next_mr=next_mr,
    )


def main_cli():
    """CLI entry point for `mr-history-serve`."""
    import argparse

    parser = argparse.ArgumentParser(description="MR History Web UI")
    parser.add_argument("--db", default="mr_history.db", help="SQLite database path")
    parser.add_argument("--port", type=int, default=5111, help="Port to run on")
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Run with gunicorn in production mode",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Run Flask in debug mode (default: off unless --prod)",
    )
    args = parser.parse_args()

    app.config["DB_PATH"] = args.db

    if args.prod:
        try:
            from werkzeug.middleware.proxy_fix import ProxyFix

            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        except ImportError:
            pass

        try:
            from gunicorn.app.base import BaseApplication

            class StandaloneApplication(BaseApplication):
                def __init__(self, app_obj, options=None):
                    self.options = options or {}
                    super().__init__()
                    self.application = app_obj

                def load_config(self):
                    for key, value in self.options.items():
                        if key in self.cfg.settings and value is not None:
                            self.cfg.set(key.lower(), value)

                def load(self):
                    return self.application

            options = {
                "bind": f"{args.host}:{args.port}",
                "workers": 2,
            }
            StandaloneApplication(app, options).run()
        except ImportError:
            print(
                "gunicorn not installed. Falling back to Flask dev server.",
                file=sys.stderr,
            )
            app.run(host=args.host, port=args.port, debug=False)
    else:
        debug = args.debug or os.environ.get("FLASK_DEBUG", "0") == "1"
        app.run(host=args.host, port=args.port, debug=debug)


if __name__ == "__main__":
    main_cli()
