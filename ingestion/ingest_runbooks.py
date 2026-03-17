import os
import glob
import re
from dataclasses import dataclass
from typing import List, Dict, Any

from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams, PointStruct

from sentence_transformers import SentenceTransformer
from rich import print


COLLECTION_NAME = "sre_runbooks"
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass
class Chunk:
    text: str
    metadata: Dict[str, Any]


def parse_metadata(md_text: str) -> Dict[str, str]:
    """
    Extract bullet metadata under a '## Metadata' section.
    Format we expect:
      ## Metadata
      - key: value
      - key2: value2
    """
    meta = {}
    # Find metadata block
    match = re.search(r"##\s+Metadata\s*(.*?)(\n##\s+|\Z)", md_text, re.S | re.I)
    if not match:
        return meta
    block = match.group(1)
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("-") and ":" in line:
            k, v = line[1:].split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


def chunk_markdown(md_text: str, filename: str) -> List[Chunk]:
    """
    Split by second-level headings (## ...).
    Each chunk = heading + content.
    """
    chunks: List[Chunk] = []
    meta = parse_metadata(md_text)

    parts = re.split(r"\n(?=##\s+)", md_text)
    title_line = md_text.splitlines()[0].strip() if md_text.strip() else filename

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Identify section heading if present
        heading_match = re.match(r"##\s+(.+)", part)
        section = heading_match.group(1).strip() if heading_match else "General"

        chunk_text = f"{title_line}\n\n{part}".strip()
        
                # Skip very low-signal sections for retrieval
        if section.lower() in {"metadata"}:
            continue

        # Skip title-only / low-signal chunks
        if section == "General" and len(part.splitlines()) <= 2:
            continue

        # Skip tiny chunks (often useless)
        if len(chunk_text) < 140:
            continue



        chunks.append(
            Chunk(
                text=chunk_text,
                metadata={
                    "source_file": os.path.basename(filename),
                    "runbook_title": title_line.replace("#", "").strip(),
                    "section": section,
                    **meta,
                },
            )
        )

    return chunks


def ensure_collection(client: QdrantClient, vector_size: int) -> None:
    existing = client.get_collections().collections
    if any(c.name == COLLECTION_NAME for c in existing):
        print(f"[yellow]Collection '{COLLECTION_NAME}' already exists.[/yellow]")
        return

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
    print(f"[green]Created collection '{COLLECTION_NAME}'.[/green]")


def main():
    # 1) Load embedding model
    print("[bold]Loading embedding model...[/bold]")
    model = SentenceTransformer(EMBED_MODEL_NAME)

    # 2) Connect to Qdrant
    client = QdrantClient(url="http://localhost:6333")

    # 3) Read runbooks
    files = sorted(glob.glob("runbooks/*.md"))
    if not files:
        raise RuntimeError("No runbooks found in runbooks/*.md")

    all_chunks: List[Chunk] = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fp:
            md = fp.read()
        chunks = chunk_markdown(md, f)
        all_chunks.extend(chunks)

    print(f"[cyan]Loaded {len(files)} runbooks → created {len(all_chunks)} chunks.[/cyan]")

    # 4) Create collection
    test_vec = model.encode("test").tolist()
    ensure_collection(client, vector_size=len(test_vec))

    # 5) Embed + upsert into Qdrant
    points: List[PointStruct] = []
    for idx, chunk in enumerate(all_chunks):
        vec = model.encode(chunk.text).tolist()
        points.append(
            PointStruct(
                id=idx,
                vector=vec,
                payload={
                    "text": chunk.text,
                    **chunk.metadata,
                },
            )
        )

    client.upsert(collection_name=COLLECTION_NAME, points=points)
    print("[bold green]✅ Ingestion complete! Chunks stored in Qdrant.[/bold green]")


if __name__ == "__main__":
    main()
