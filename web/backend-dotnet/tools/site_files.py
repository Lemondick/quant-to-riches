"""Public site inventory and account-scoped evidence path relocation."""

from pathlib import Path, PurePosixPath

PRIVATE_PARTS = {"server-secrets", "research-secrets", "secrets", ".env"}


def private_file(relative: Path) -> bool:
    return (
        any(part.lower() in PRIVATE_PARTS for part in relative.parts)
        or relative.name.lower().startswith(".env")
        or relative.suffix.lower() in {".key", ".pem", ".pfx", ".p12"}
        or relative.name.lower() in {"agent_key.txt", "agent_credentials.env"}
    )


def evidence_relative(raw: str, user: str, role: str, files: dict) -> str | None:
    """Match preserved suffixes after moving a frozen Windows/Linux site.

    Resolve only within the owning account's file tree. Ambiguous suffixes fail
    rather than selecting another account's price evidence.
    """
    original = PurePosixPath(raw.replace("\\", "/")).parts
    if not original:
        return None
    namespaces = [index for index, part in enumerate(original[:-1]) if part == "users"]
    if role != "admin" and namespaces and original[namespaces[-1] + 1] != user:
        return None
    candidates = []
    for relative in files:
        if role == "admin":
            if relative.startswith("users/"):
                continue
            local = PurePosixPath(relative).parts
        else:
            prefix = "users/" + user + "/"
            if not relative.startswith(prefix):
                continue
            # Keep users/<owner> while matching: a root-level file still has a
            # unique account suffix. Never guess from its filename alone.
            local = PurePosixPath(relative).parts
        score = 0
        for left, right in zip(reversed(original), reversed(local), strict=False):
            if left != right:
                break
            score += 1
        if score >= 2 and local[-1] == original[-1]:
            candidates.append((score, relative))
    if not candidates:
        return None
    best = max(score for score, _ in candidates)
    selected = [relative for score, relative in candidates if score == best]
    if len(selected) != 1:
        raise ValueError("A snapshot evidence path is ambiguous in the frozen site")
    return selected[0]


def relocate_evidence(cursor, destination: Path, files: dict):
    cursor.execute(
        "SELECT s.user_id,s.id,s.path,s.status,u.role FROM data_snapshots s "
        "JOIN users u ON u.id=s.user_id ORDER BY s.user_id,s.id"
    )
    changes = []
    for user, identity, raw, status, role in cursor.fetchall():
        relative = evidence_relative(raw, user, role, files)
        if relative is None:
            if status == "valid":
                raise ValueError("A valid snapshot has no preserved evidence file")
            continue
        target = str(destination / relative)
        if raw != target:
            cursor.execute(
                "UPDATE data_snapshots SET path=%s WHERE user_id=%s AND id=%s",
                (target, user, identity),
            )
            changes.append(
                {
                    "user_id": user,
                    "snapshot_id": identity,
                    "original_path": raw,
                    "path": target,
                }
            )
    return changes
