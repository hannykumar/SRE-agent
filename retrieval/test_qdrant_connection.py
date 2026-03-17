from qdrant_client import QdrantClient
from rich import print

def main():
    client = QdrantClient(url="http://localhost:6333")

    # This call will fail if Qdrant isn't reachable, so it's a good "health check".
    collections = client.get_collections()

    print("[bold green]✅ Connected to Qdrant successfully![/bold green]")
    print("[bold cyan]Collections response:[/bold cyan]")
    print(collections)

if __name__ == "__main__":
    main()
