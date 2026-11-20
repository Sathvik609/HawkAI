import concurrent.futures as cf
import json
import sys
from collections import Counter

from config import DATA_DIR
from retrieval import NarrativeIndex, Searcher

ALWAYS = ["The Beauty Myth", "Looking-Glass Self", "Anomie", "Presentation of Self", "Collective Consciousness",
          "White Privilege", "Alienation", "Social Class", "Gender Roles", "Socialization", "Racial Formation",
          "Dramaturgy", "Stigma", "Social Construction of Race", "Power Elite", "Intersectionality"]


def main():
    index = NarrativeIndex()
    searcher = Searcher(index)
    counts = Counter(c.strip() for n in index.narratives for c in n["concepts"])
    concepts = list(dict.fromkeys([*sys.argv[1:], *ALWAYS, *[c for c, _ in counts.most_common(40)]]))
    print(f"Warming {len(concepts)} concepts...")
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for concept, result in zip(concepts, pool.map(searcher.search, concepts)):
            print(f"  {concept:40s} -> {result['concept']}: {len(result['options'])} options")
    (DATA_DIR / "warm_searches.json").write_text(json.dumps(searcher.snapshot(), indent=1, ensure_ascii=False))
    print(f"Saved {len(searcher.concepts)} concepts to data/warm_searches.json")


if __name__ == "__main__":
    main()
