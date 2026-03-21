def chunk_documents(documents: list[dict], chunk_size: int = 500, overlap: int = 50) -> list[dict]:
    chunks = []
    for doc in documents:
        content = doc["content"]
        source = doc["source"]
        start = 0
        chunk_index = 0
        while start < len(content):
            end = start + chunk_size
            chunks.append({
                "content": content[start:end],
                "source": source,
                "chunk_index": chunk_index,
            })
            chunk_index += 1
            start += chunk_size - overlap
    return chunks
