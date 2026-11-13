import pytest

from app.core.text import extract_numbers, overlap, quote_in_text, split_sentences
from app.rag.chunking import chunk_text


def test_extract_numbers_canonicalises():
    assert extract_numbers("Sales of 412,000 units, up 38 percent and 6.40% share in 2024") == {"412000", "38", "6.4", "2024"}


def test_quote_in_text_tolerates_whitespace_and_case_but_not_paraphrase():
    src = "The market grew 38 percent  year on year\nin fiscal year 2024."
    assert quote_in_text("the market grew 38 percent year on year in fiscal year 2024", src)
    assert not quote_in_text("the market grew 40 percent year on year", src)
    assert quote_in_text("The market grew 38 percent ... in fiscal year 2024", src)
    assert not quote_in_text("short", src)  # too short to count as evidence


def test_overlap_is_containment():
    assert overlap("electric vehicle sales", "Electric vehicle sales rose") == 1.0
    assert overlap("battery prices", "charging points") == 0.0


def test_split_sentences():
    assert split_sentences("One fact here. Two facts here! Three?") == ["One fact here.", "Two facts here!", "Three?"]


def test_chunking_respects_size_and_overlap():
    para = " ".join(f"Sentence number {i} talks about electric vehicles." for i in range(40))
    text = "\n\n".join([para] * 5)
    chunks = chunk_text(text, chunk_size=120, overlap=30)
    assert len(chunks) > 5
    assert all(c.token_count <= 120 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    # consecutive chunks share overlapping text
    shared = sum(1 for a, b in zip(chunks, chunks[1:], strict=False) if a.text.split("\n\n")[-1] in b.text or a.text[-40:] in b.text)
    assert shared >= len(chunks) // 2


def test_chunking_small_doc_single_chunk_and_validation():
    assert len(chunk_text("A short document.", 100, 10)) == 1
    with pytest.raises(ValueError):
        chunk_text("x", 100, 100)
