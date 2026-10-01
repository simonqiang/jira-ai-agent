"""Chunk Jira issue content for the retrieval index.

Descriptions arrive as Atlassian Document Format; chunks follow document
nodes (paragraphs, lists, tables, panels) rather than splitting raw text, so
each chunk stays a coherent block and carries its nearest heading. The summary
is indexed as its own chunk — titles carry most of the similarity signal
(spec §7).
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

# Embedding APIs reject very long inputs; long blocks are split, never dropped.
_MAX_CHARS = 8000


@dataclass(frozen=True)
class Chunk:
    chunk_kind: str  # "summary" | "description"
    chunk_index: int
    heading: str | None
    content: str

    @property
    def content_hash(self) -> str:
        return sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def embed_text(self) -> str:
        """The text embedded for this chunk (heading included as context)."""
        return f"{self.heading}\n{self.content}" if self.heading else self.content


def build_chunks(*, summary: str, description: object) -> list[Chunk]:
    """Summary chunk plus one chunk per description document block."""
    chunks: list[Chunk] = []
    summary = summary.strip()
    if summary:
        chunks.append(Chunk("summary", 0, None, summary))
    description_index = 0
    for heading, block in adf_blocks(description):
        for split in range(0, len(block), _MAX_CHARS):
            chunks.append(
                Chunk("description", description_index, heading, block[split : split + _MAX_CHARS])
            )
            description_index += 1
    return chunks


def adf_blocks(node: object, heading: str | None = None) -> list[tuple[str | None, str]]:
    """(nearest heading, text) per document block, in document order.

    Known leaf node types render explicitly; any other container (panel,
    expand, blockquote, codeBlock, …) recurses into its content with the
    current heading, so unknown ADF extensions degrade to plain text instead
    of vanishing.
    """
    if isinstance(node, str):
        # Some Jira fields carry plain strings instead of ADF documents.
        return [(None, node.strip())] if node.strip() else []
    if not isinstance(node, dict):
        return []
    node_type = node.get("type")
    if node_type == "heading":
        heading = _node_text(node) or heading
        return []
    if node_type in {"paragraph", "text"}:
        text = _node_text(node)
        return [(heading, text)] if text else []
    if node_type in {"bulletList", "orderedList", "decisionList", "taskList"}:
        items = [text for text in (_node_text(item) for item in node.get("content") or ()) if text]
        return [(heading, "\n".join(f"- {item}" for item in items))] if items else []
    if node_type == "table":
        rows = [
            " | ".join(cell for cell in (_node_text(c) for c in row.get("content") or ()) if cell)
            for row in node.get("content") or ()
        ]
        rows = [row for row in rows if row.strip(" |")]
        return [(heading, "\n".join(rows))] if rows else []
    blocks: list[tuple[str | None, str]] = []
    current = heading
    for child in node.get("content") or ():
        if isinstance(child, dict) and child.get("type") == "heading":
            # The heading belongs to its *following* siblings, so track it here
            # in the sibling loop instead of inside the recursive call.
            current = _node_text(child) or current
            continue
        blocks.extend(adf_blocks(child, current))
    return blocks


def _node_text(node: object) -> str:
    """Plain text of one ADF subtree (mirrors jira_text's visitor)."""
    parts: list[str] = []

    def visit(item: object) -> None:
        if not isinstance(item, dict):
            return
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
        for child in item.get("content") or ():
            visit(child)
        if item.get("type") in {"paragraph", "heading", "listItem"} and parts:
            parts.append("\n")

    visit(node)
    return "".join(parts).strip()
