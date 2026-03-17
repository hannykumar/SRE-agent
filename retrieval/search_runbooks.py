from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from rich import print

COLLECTION_NAME = "sre_runbooks"
EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def main():
    # Connect to Qdrant
    client = QdrantClient(url="http://localhost:6333")

    # Load embedding model (same one used during ingestion)
    model = SentenceTransformer(EMBED_MODEL_NAME)

    print("[bold green]Runbook Search Debugger[/bold green]")
    print("Type an incident description. Type 'q' to quit.\n")

    while True:
        query = input("Incident> ").strip()
        if query.lower() in {"q", "quit", "exit"}:
            break
        if not query:
            continue

        # Embed query and search
        qvec = model.encode(query).tolist()

        results = client.search(
            collection_name=COLLECTION_NAME,
            query_vector=qvec,
            limit=5,
            with_payload=True,
        )

        print("\n[bold cyan]Top Matches[/bold cyan]")
        for i, r in enumerate(results, 1):
            p = r.payload or {}

            print(f"\n[bold]{i}. score={r.score:.4f}[/bold]")
            print(f"source_file : {p.get('source_file')}")
            print(f"incident_type: {p.get('incident_type')}")
            print(f"section     : {p.get('section')}")

            text = (p.get("text") or "").replace("\n", " ")
            preview = text[:350] + ("..." if len(text) > 350 else "")
            print(f"preview     : {preview}")

        print("\n" + "-" * 60 + "\n")


if __name__ == "__main__":
    main()
