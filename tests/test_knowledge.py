from backend.config import ROOT
from backend.knowledge import Corpus, Knowledge


def test_sample_data_has_stable_unique_sources(config):
    corpus = Corpus(ROOT / "data" / "sample_courses.csv")
    assert len(corpus.rows) == len(corpus.parents) == 16
    assert corpus.source("S01")["version"] == corpus.version
    kb = Knowledge(config)
    result = kb.search("重排如何处理候选片段", "01")
    assert result["mode"] == "demo_lexical"
    assert "S02" in [s["id"] for s in result["sources"]]
    assert all(s["doc_id"] == "01" for s in result["sources"])
    diagnostics = result["diagnostics"]
    assert diagnostics["query"] == "重排如何处理候选片段"
    assert set(diagnostics["lexical"]) <= {c["child_id"] for c in diagnostics["candidates"]}
    assert all(c["parent_id"] in corpus.parents for c in diagnostics["candidates"])


def test_unknown_lexical_query_does_not_fabricate_source(config):
    assert Knowledge(config).search("zzzznonexistentword9999")["sources"] == []
