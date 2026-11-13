from vault import VaultIndex, chunk_document, normalize


def make_index(tmp_path):
    vault = tmp_path / "vault" / "notes"
    vault.mkdir(parents=True)
    (vault / "turing.md").write_text(
        "# Turing\n\nInstructions and data share the same tape.\n\n## Halting\n\n"
        "No general procedure decides whether an arbitrary program halts.\n",
        encoding="utf-8",
    )
    index = VaultIndex(tmp_path / "index.sqlite")
    stats = index.rebuild(tmp_path / "vault")
    return index, stats


def test_chunk_ids_are_stable_and_uris_are_posix():
    a = chunk_document("vault://notes/a.md", "# H\n\nSome text.")
    b = chunk_document("vault://notes/a.md", "# H\n\nSome text.")
    assert a[0].chunk_id == b[0].chunk_id
    assert a[0].heading == "H"


def test_index_and_search(tmp_path):
    index, stats = make_index(tmp_path)
    assert stats == {"files": 1, "chunks": 2}
    hits = index.search("halting program")
    assert hits and hits[0]["uri"] == "vault://notes/turing.md"


def test_verify_quote_normalizes_whitespace_and_quotes(tmp_path):
    index, _ = make_index(tmp_path)
    chunk_id = index.search("tape")[0]["chunk_id"]
    assert index.verify_quote(chunk_id, "data  share the\nsame tape")["verified"]
    assert not index.verify_quote(chunk_id, "data share a different tape")["verified"]
    assert not index.verify_quote("0" * 16, "anything")["verified"]


def test_audit_draft_reports_uncited_and_unknown(tmp_path):
    index, _ = make_index(tmp_path)
    chunk_id = index.search("tape")[0]["chunk_id"]
    draft = (
        f"Turing put code and data on one tape [@{chunk_id}]. "
        "Everyone agrees this was obvious. "
        "This one cites nothing real [@ffffffffffffffff]. "
        "This is my own view [unsourced]."
    )
    report = index.audit_draft(draft)
    assert report["cited_sentences"] == 2
    assert report["uncited_sentences"] == ["Everyone agrees this was obvious."]
    assert report["unknown_citations"] == ["ffffffffffffffff"]
    assert not report["clean"]


def test_normalize_curly_quotes():
    assert normalize("“hello”  it’s") == '"hello" it\'s'
