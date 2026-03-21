from pathlib import Path


def load_documents(path: str) -> list[dict]:
    documents = []
    for file in Path(path).rglob("*"):
        if file.suffix in (".md", ".txt"):
            documents.append({
                "content": file.read_text(encoding="utf-8"),
                "source": str(file),
            })
    return documents
