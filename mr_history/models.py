"""SQLAlchemy models for GitLab MR history analysis."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, doc="GitLab user ID")
    username = Column(String, nullable=False, unique=True)
    name = Column(String, nullable=False)
    state = Column(String, nullable=False)
    avatar_url = Column(String)
    web_url = Column(String)

    # relationships
    authored_mrs = relationship(
        "MergeRequest", back_populates="author", foreign_keys="MergeRequest.author_id"
    )
    merged_mrs = relationship(
        "MergeRequest",
        back_populates="merged_by",
        foreign_keys="MergeRequest.merged_by_id",
    )
    comments = relationship("Comment", back_populates="author")

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.id})>"


class MergeRequest(Base):
    __tablename__ = "merge_requests"

    id = Column(Integer, primary_key=True, doc="GitLab internal MR ID")
    iid = Column(Integer, nullable=False, doc="Project-scoped MR number")
    project_id = Column(Integer, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text)
    state = Column(String, nullable=False)
    source_branch = Column(String)
    target_branch = Column(String)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime)
    merged_at = Column(DateTime)
    closed_at = Column(DateTime)
    user_notes_count = Column(Integer, default=0)
    upvotes = Column(Integer, default=0)
    downvotes = Column(Integer, default=0)
    web_url = Column(String)
    labels = Column(Text, doc="Comma-separated label names")
    has_conflicts = Column(Boolean)
    draft = Column(Boolean)
    user_role = Column(
        String,
        doc="User's role on this MR: 'assignee', 'reviewer', or 'both'",
    )

    # foreign keys
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    merged_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # relationships
    author = relationship(
        "User", back_populates="authored_mrs", foreign_keys=[author_id]
    )
    merged_by = relationship(
        "User", back_populates="merged_mrs", foreign_keys=[merged_by_id]
    )
    comments = relationship("Comment", back_populates="merge_request")
    discussions = relationship("Discussion", back_populates="merge_request")

    def __repr__(self) -> str:
        return f"<MR !{self.iid} {self.title[:40]}>"


class Discussion(Base):
    __tablename__ = "discussions"

    id = Column(String, primary_key=True, doc="GitLab discussion ID (SHA)")
    merge_request_id = Column(
        Integer, ForeignKey("merge_requests.id"), nullable=False
    )
    individual_note = Column(
        Boolean, default=True, doc="True=standalone, False=threaded"
    )

    # relationships
    merge_request = relationship("MergeRequest", back_populates="discussions")
    comments = relationship(
        "Comment", back_populates="discussion", order_by="Comment.created_at"
    )

    def __repr__(self) -> str:
        return f"<Discussion {self.id[:12]} on MR {self.merge_request_id}>"


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, doc="GitLab note ID")
    merge_request_id = Column(
        Integer, ForeignKey("merge_requests.id"), nullable=False
    )
    discussion_id = Column(
        String, ForeignKey("discussions.id"), nullable=True
    )
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime)
    system = Column(Boolean, default=False, doc="System-generated note")
    resolvable = Column(Boolean, default=False)
    resolved = Column(Boolean, default=False)
    noteable_type = Column(String)
    type = Column(String, doc="Note type: DiffNote, DiscussionNote, etc.")
    confidential = Column(Boolean, default=False)

    # Diff position fields (populated for DiffNote types)
    diff_old_path = Column(String, doc="File path before change")
    diff_new_path = Column(String, doc="File path after change")
    diff_old_line = Column(Integer, doc="Line number in old file")
    diff_new_line = Column(Integer, doc="Line number in new file")
    diff_position_type = Column(String, doc="'text' or 'file'")
    diff_line_range_start = Column(Integer, doc="Start line of range")
    diff_line_range_end = Column(Integer, doc="End line of range")
    diff_line_range_type = Column(
        String, doc="'new' or 'old' — which side the range refers to"
    )
    diff_base_sha = Column(String)
    diff_head_sha = Column(String)
    diff_commit_id = Column(String, doc="Specific commit commented on")

    # relationships
    merge_request = relationship("MergeRequest", back_populates="comments")
    discussion = relationship("Discussion", back_populates="comments")
    author = relationship("User", back_populates="comments")

    def __repr__(self) -> str:
        return f"<Comment {self.id} on MR {self.merge_request_id}>"

    @property
    def has_diff_position(self) -> bool:
        return self.diff_new_path is not None

    @property
    def diff_file(self) -> str | None:
        return self.diff_new_path or self.diff_old_path

    @property
    def diff_line_display(self) -> str | None:
        """Human-readable line reference."""
        if not self.has_diff_position:
            return None
        if self.diff_position_type == "file":
            return "file-level"
        if self.diff_line_range_start and self.diff_line_range_end:
            if self.diff_line_range_start != self.diff_line_range_end:
                return f"L{self.diff_line_range_start}-{self.diff_line_range_end}"
        if self.diff_new_line:
            return f"L{self.diff_new_line}"
        if self.diff_old_line:
            return f"L{self.diff_old_line} (old)"
        return None


class FileSnapshot(Base):
    __tablename__ = "file_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    commit_sha = Column(String, nullable=False, doc="Git commit SHA the file was fetched at")
    file_path = Column(String, nullable=False, doc="Path to the file in the repo")
    content = Column(Text, nullable=False, doc="Full file content at this commit")
    line_count = Column(Integer, doc="Number of lines in the file")
    fetched_at = Column(DateTime, nullable=False)

    __table_args__ = (
        # Unique constraint on (commit_sha, file_path)
        {"sqlite_autoincrement": True},
    )

    # We need a unique constraint but SQLAlchemy with SQLite autoincrement
    # needs it done this way. Add index manually after table creation.

    def __repr__(self) -> str:
        return f"<FileSnapshot {self.commit_sha[:12]}:{self.file_path}>"

    def get_line(self, line_number: int) -> str | None:
        """Get a specific line (1-indexed)."""
        if not self.content or line_number < 1 or line_number > (self.line_count or 0):
            return None
        lines = self.content.split("\n")
        if line_number > len(lines):
            return None
        return lines[line_number - 1]

    def get_context(
        self, center_line: int | None = None, context_lines: int = 5, side: str = "new"
    ) -> list[dict] | None:
        """
        Return lines around a target line for diff context.

        Returns list of dicts: [{"line": int, "content": str, "is_target": bool}]
        """
        if not self.content:
            return None

        target = center_line
        if target is None:
            return None

        all_lines = self.content.split("\n")
        start = max(1, target - context_lines)
        end = min(len(all_lines), target + context_lines)

        result = []
        for i in range(start, end + 1):
            result.append({
                "line": i,
                "content": all_lines[i - 1] if i <= len(all_lines) else "",
                "is_target": i == target,
            })
        return result


def get_engine(db_path: str = "mr_history.db"):
    return create_engine(f"sqlite:///{db_path}", echo=False)


def get_session(db_path: str = "mr_history.db"):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)

    # Ensure unique index on file_snapshots
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_file_snapshots_sha_path "
                "ON file_snapshots (commit_sha, file_path)"
            )
        )
        conn.commit()

    Session = sessionmaker(bind=engine)
    return Session()
