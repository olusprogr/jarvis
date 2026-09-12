"""Tools giving the agent read/write access to the Jarvis Obsidian vault.
This is Jarvis's persistent memory: facts about the user, notes, logs of past tasks.
"""
import datetime
from pathlib import Path
from .. import config

VAULT = config.VAULT_DIR


def _safe_path(rel_path: str) -> Path:
    """Resolves rel_path inside the vault, refuses to escape it."""
    p = (VAULT / rel_path).resolve()
    if VAULT not in p.parents and p != VAULT:
        raise ValueError("Path escapes the vault, refused.")
    return p


def obsidian_write_note(path: str, content: str, append: bool = False) -> str:
    """Write (or append to) a markdown note in the Obsidian vault.

    Args:
        path: relative path inside the vault, e.g. 'Personen/Olus.md' or 'Log/2026-09-12.md'.
            Add '.md' yourself. Use subfolders to keep things organized
            (e.g. 'Fakten/', 'Log/', 'Projekte/', 'Ideen/').
        content: markdown content to write.
        append: if true, appends with a timestamp separator instead of overwriting.
    """
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if append and p.exists():
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\n\n---\n*{stamp}*\n\n{content}\n")
    else:
        p.write_text(content, encoding="utf-8")
    return f"OK, gespeichert unter {path}"


def obsidian_read_note(path: str) -> str:
    """Read the full content of a markdown note from the Obsidian vault.

    Args:
        path: relative path inside the vault, e.g. 'Personen/Olus.md'.
    """
    p = _safe_path(path)
    if not p.exists():
        return f"Notiz {path} existiert nicht."
    return p.read_text(encoding="utf-8")


def obsidian_list_notes(folder: str = "") -> list[str]:
    """List all markdown note paths in the vault (optionally inside a subfolder).

    Args:
        folder: relative subfolder path, empty string for vault root (recursive).
    """
    base = _safe_path(folder) if folder else VAULT
    if not base.exists():
        return []
    return sorted(str(p.relative_to(VAULT)) for p in base.rglob("*.md"))


def obsidian_search(query: str) -> str:
    """Full-text search across all notes in the vault. Case-insensitive substring match.

    Args:
        query: text to search for.
    """
    query_lower = query.lower()
    hits = []
    for p in VAULT.rglob("*.md"):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if query_lower in text.lower():
            for i, line in enumerate(text.splitlines(), 1):
                if query_lower in line.lower():
                    hits.append(f"{p.relative_to(VAULT)}:{i}: {line.strip()}")
    if not hits:
        return "Keine Treffer."
    return "\n".join(hits[:40])
