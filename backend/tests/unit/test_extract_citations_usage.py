import io

import pytest

from app.core.errors import BudgetExceeded, ExtractionError
from app.llm.usage import Limits, UsageTracker
from app.rag.citations import linkify, validate_citations
from app.retrieval.extract import extract_html, extract_pdf

HTML = """<html><head><title>EV Report</title><meta property="article:published_time" content="2025-03-14"></head>
<body><nav>Home | About | Login</nav><article><h1>EV Report</h1>
<p>The electric vehicle market recorded sales of 412,000 vehicles in fiscal year 2024 according to registration data.</p>
<p>Electric two-wheelers accounted for 61 percent of all registrations, while electric cars accounted for 9 percent.</p>
<p>Public charging infrastructure expanded to 18,500 charging points by March 2025 across the country.</p>
</article><footer>Copyright 2025</footer></body></html>"""


def test_extract_html_main_content_and_metadata():
    doc = extract_html(HTML, "https://example.com/r")
    assert "412,000" in doc.text and "18,500" in doc.text
    assert "Login" not in doc.text
    assert doc.title == "EV Report"
    assert doc.published_at == "2025-03-14"


def test_extract_html_rejects_empty_pages():
    with pytest.raises(ExtractionError):
        extract_html("<html><body><p>Hi</p></body></html>")


def _make_pdf(text: str) -> bytes:
    # Minimal valid PDF with one text line per paragraph.
    lines = [ln for ln in text.split("\n") if ln]
    content = "BT /F1 11 Tf 50 750 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_extract_pdf():
    body = _make_pdf("\n".join(f"Line {i}: battery pack prices fell 14 percent in 2024 to 118 dollars." for i in range(8)))
    doc = extract_pdf(body)
    assert "battery pack prices fell 14 percent" in doc.text
    assert doc.method == "pypdf"


def test_extract_pdf_garbage():
    with pytest.raises(ExtractionError):
        extract_pdf(b"%PDF-1.4 not really a pdf")


def test_citation_validation_strips_unknown_labels():
    chk = validate_citations("Sales rose [S1][S9]. Prices fell [S2].", {"S1", "S2"})
    assert chk.used == ["S1", "S2"] and chk.removed == ["S9"]
    assert "[S9]" not in chk.text
    assert linkify(chk.text, {"S1": "https://a.example", "S2": None}) == "Sales rose [S1](https://a.example). Prices fell [S2]."


def test_usage_budget_enforcement():
    t = UsageTracker(limits=Limits(max_tokens=1000, max_tool_calls=2))
    t.record_llm(400, 100)
    t.check()
    t.record_tool("search", True)
    t.record_tool("fetch", False)
    assert t.tool_failures == 1
    with pytest.raises(BudgetExceeded) as e:
        t.check()
    assert e.value.limit == "max_tool_calls"
    restored = UsageTracker.restore(t.snapshot(), Limits())
    assert restored.tool_calls == 2 and restored.prompt_tokens == 400
