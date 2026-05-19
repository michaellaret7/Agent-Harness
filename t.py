"""
Minimal image embedding + search demo using Voyage AI's hosted multimodal model.
No local model weights, no GPU, just API calls.

Install:
    pip install voyageai pillow numpy

Setup:
    export VOYAGE_API_KEY=your_key_here
    (get a key at https://www.voyageai.com/)

Usage:
    python image_search_hosted.py
"""

from pathlib import Path
import numpy as np
from PIL import Image
import voyageai
from dotenv import load_dotenv

load_dotenv()

vo = voyageai.Client()  # reads VOYAGE_API_KEY from env
MODEL = "voyage-multimodal-3"


def embed_image(image_path: str) -> np.ndarray:
    """Embed an image via Voyage's hosted endpoint."""
    img = Image.open(image_path).convert("RGB")
    # Voyage takes a list of "inputs", each input is a list of content items
    # (text strings and/or PIL images interleaved).
    result = vo.multimodal_embed(
        inputs=[[img]],
        model=MODEL,
        input_type="document",  # use "document" for indexed items
    )
    vec = np.array(result.embeddings[0])
    return vec / np.linalg.norm(vec)  # normalize for cosine sim


def embed_text(text: str) -> np.ndarray:
    """Embed a text query into the same vector space."""
    result = vo.multimodal_embed(
        inputs=[[text]],
        model=MODEL,
        input_type="query",  # use "query" for search queries
    )
    vec = np.array(result.embeddings[0])
    return vec / np.linalg.norm(vec)


def build_index(image_paths: list[str]):
    """Embed each image in the given list. Returns (paths, matrix of vectors)."""
    paths = []
    vectors = []

    for path in image_paths:
        p = Path(path)

        if not p.exists():
            print(f"skipping {p.name} (not found)")
            continue

        print(f"embedding {p.name}")
        paths.append(str(p))
        vectors.append(embed_image(str(p)))

    return paths, np.stack(vectors)


def search(query_vec: np.ndarray, paths: list, index: np.ndarray, k: int = 5):
    """Cosine similarity via dot product (vectors are pre-normalized)."""
    scores = index @ query_vec
    top_idx = np.argsort(-scores)[:k]
    return [(paths[i], float(scores[i])) for i in top_idx]


if __name__ == "__main__":
    IMAGES = ["image_1.jpg", "image_2.jpg", "image_3.jpg"]
    paths, index = build_index(IMAGES)
    print(f"\nindexed {len(paths)} images, vector dim = {index.shape[1]}\n")

    # Text query
    text_query = "a red car"
    print(f"text query: {text_query!r}")
    for path, score in search(embed_text(text_query), paths, index, k=3):
        print(f"  {score:.3f}  {path}")

    # Image query (use first indexed image just to demo)
    if paths:
        print(f"\nimage query: {paths[0]}")
        for path, score in search(embed_image(paths[0]), paths, index, k=3):
            print(f"  {score:.3f}  {path}")