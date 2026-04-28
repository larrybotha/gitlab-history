"""Resolve GitLab project and user IDs from glab CLI or environment.

Resolution order:
1. MR_HISTORY_PROJECT_ID / MR_HISTORY_USER_ID env vars (explicit override)
2. glab CLI auto-detection (repo remote + authenticated user)
3. Error if neither available
"""

import json
import os
import subprocess
import sys


def _glab_api(endpoint: str, repo: str | None = None) -> dict | None:
    """Call glab api, optionally with --repo flag."""
    cmd = ["glab", "api", endpoint]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _detect_repo_from_remote() -> str | None:
    """Try to derive 'group/project' from git remotes in current dir."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return None
        url = result.stdout.strip()
        # https://gitlab.com/group/project.git → group/project
        # git@gitlab.com:group/project.git → group/project
        if url.startswith("https://"):
            path = url.replace("https://", "").split("/", 1)[-1]
        elif url.startswith("git@"):
            path = url.split(":", 1)[-1]
        else:
            return None
        return path.removesuffix(".git")
    except Exception:
        return None


def resolve_project_id(repo: str | None = None) -> int:
    """Resolve GitLab project ID.

    Parameters
    ----------
    repo : str or None
        Explicit 'group/project' path. If None, auto-detects from env var
        or git remote.

    Returns
    -------
    int
        GitLab project ID.

    Raises
    ------
    SystemExit
        If project ID cannot be resolved.
    """
    env_val = os.environ.get("MR_HISTORY_PROJECT_ID")
    if env_val:
        return int(env_val)

    # Try auto-detect from repo
    repo_path = repo or _detect_repo_from_remote()
    if repo_path:
        data = _glab_api("projects/:id", repo=repo_path)
        if data and "id" in data:
            return data["id"]

    print(
        "ERROR: Cannot determine GitLab project ID.\n"
        "Set MR_HISTORY_PROJECT_ID env var, or run from a directory with a "
        "GitLab git remote, or pass --repo group/project.",
        file=sys.stderr,
    )
    sys.exit(1)


def resolve_user_id() -> int:
    """Resolve GitLab user ID for the authenticated glab user.

    Returns
    -------
    int
        GitLab user ID.

    Raises
    ------
    SystemExit
        If user ID cannot be resolved.
    """
    env_val = os.environ.get("MR_HISTORY_USER_ID")
    if env_val:
        return int(env_val)

    data = _glab_api("user")
    if data and "id" in data:
        return data["id"]

    print(
        "ERROR: Cannot determine GitLab user ID.\n"
        "Set MR_HISTORY_USER_ID env var, or ensure `glab auth login` has been run.",
        file=sys.stderr,
    )
    sys.exit(1)
