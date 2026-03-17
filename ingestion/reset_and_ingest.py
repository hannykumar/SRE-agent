from qdrant_client import QdrantClient
from rich import print
import subprocess
import sys

COLLECTION_NAME = "sre_runbooks"

def main():
    client = QdrantClient(url="http://localhost:6333")

    # Delete collection if exists
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME in existing:
        client.delete_collection(collection_name=COLLECTION_NAME)
        print(f"[yellow]Deleted collection '{COLLECTION_NAME}'.[/yellow]")

    # Run ingestion script
    print("[cyan]Re-ingesting runbooks...[/cyan]")
    result = subprocess.run([sys.executable, "ingestion/ingest_runbooks.py"], check=True)
    print("[bold green]✅ Reset + ingestion complete.[/bold green]")

if __name__ == "__main__":
    main()
