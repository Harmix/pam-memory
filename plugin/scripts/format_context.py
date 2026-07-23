"""Format retrieve result into bounded Claude hook context."""

from __future__ import annotations


def format_memory_context(
    *,
    content_text: str,
    sources: list[str] | None = None,
    max_chars: int = 4000,
) -> str:
    text = content_text.strip()
    if not text:
        return ""

    lines = [
        "## PAM company memory (auto-retrieved)",
        "",
        text,
    ]

    if sources:
        lines.extend(["", "Sources:"])
        for path in sources[:10]:
            lines.append(f"- {path}")

    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block

    truncated = block[: max_chars - 20].rstrip()
    return f"{truncated}\n\n...(truncated)"
