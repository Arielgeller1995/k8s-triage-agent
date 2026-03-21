import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


class TFIDFRetriever:
    def __init__(self):
        self._vectorizer = TfidfVectorizer()
        self._matrix = None
        self._chunks = []

    def build_index(self, chunks: list[dict]) -> None:
        self._chunks = chunks
        self._matrix = self._vectorizer.fit_transform(c["content"] for c in chunks)

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        query_vec = self._vectorizer.transform([query])
        scores = cosine_similarity(query_vec, self._matrix)[0]
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [
            {**self._chunks[i], "score": float(scores[i])}
            for i in top_indices
        ]
