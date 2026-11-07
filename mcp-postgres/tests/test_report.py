"""Report outline whitelist, artifact resolution and the three renderers."""

import base64
import io
from dataclasses import dataclass

import pytest

from mcpcluster_common import chart as ch
from mcpcluster_common import report as rp

A1, A2, A3 = "art_" + "1" * 32, "art_" + "2" * 32, "art_" + "3" * 32
INJECT = 'A #read("/etc/passwd") *b* $x$ #import "@preview/cetz:0.2.0"'


@dataclass
class Meta:
    kind: str
    title: str
    server_id: str
    server_type: str = "postgres"
    created_at: str = "2026-09-24T01:00:00+00:00"


def _arts():
    _, png = ch.render(ch.parse_chart({"type": "bar", "x": {"field": "k"}, "y": {"field": "n"}}), [{"k": "a", "n": 1}])
    return {
        A1: (Meta("compute", "stats", "srv_a"), {"op": "describe", "result": {"count": 1234567, "mean": 12.3456789, "max": 99}, "source": {"sql": "select 1"}}),
        A2: (Meta("table", "programs", "srv_b", "clickhouse"), {"rows": [{"program": INJECT, "reports": 10}, {"program": "p2", "reports": 5}], "source": {"sql": "select 2"}}),
        A3: (Meta("chart", "chart", "srv_a"), {"png_base64": base64.b64encode(png).decode(), "rows": []}),
    }


def _outline(**over):
    o = {"title": "9월 보고서", "subtitle": "현황", "author": "FTG", "sections": [
        {"heading": "요약", "blocks": [{"type": "text", "text": f"첫 문단.\n\n- 항목 하나\n- {INJECT}"},
                                      {"type": "kpi", "artifact_id": A1, "fields": ["count", "mean"], "labels": {"count": "제보 수"}}]},
        {"heading": "상세", "blocks": [{"type": "table", "artifact_id": A2, "max_rows": 1}, {"type": "chart", "artifact_id": A3, "caption": "차트"}]}]}
    o.update(over)
    return o


def test_outline_whitelist():
    for bad in (
        {"title": "t", "sections": []},
        {"title": "t", "sections": [{"heading": "h", "blocks": [{"type": "html", "html": "<script>"}]}]},
        {"title": "t", "sections": [{"heading": "h", "blocks": [{"type": "kpi", "artifact_id": A1, "value": 42}]}]},  # no typed numbers
        {"title": "t", "template": "evil.typ", "sections": [{"heading": "h"}]},
        {"title": "t", "sections": [{"heading": "h", "blocks": [{"type": "text", "text": "x"}] * 40}] * 4},  # > MAX_BLOCKS
    ):
        with pytest.raises(rp.ReportRejected):
            rp.parse_outline(bad)
    assert rp.artifact_ids(rp.parse_outline(_outline())) == [A1, A2, A3]


def test_resolve_takes_figures_from_artifacts_only():
    r = rp.resolve(rp.parse_outline(_outline()), _arts())
    kpi = r.sections[0].blocks[1]
    assert kpi.items == [("제보 수", "1,234,567"), ("mean", "12.3457")]
    tbl = r.sections[1].blocks[0]
    assert tbl.columns == ["program", "reports"] and tbl.rows == [[INJECT, "10"]] and tbl.truncated_from == 2
    assert [s["query"] for s in r.sources] == ["select 1", "select 2", ""]


@pytest.mark.parametrize("block,match", [
    ({"type": "kpi", "artifact_id": A2}, "kpi needs a compute"),
    ({"type": "kpi", "artifact_id": A1, "fields": ["nope"]}, "not in the result"),
    ({"type": "chart", "artifact_id": A1}, "not a chart"),
    ({"type": "table", "artifact_id": A2, "columns": ["nope"]}, "not in the rows"),
])
def test_resolve_rejects_mismatched_blocks(block, match):
    with pytest.raises(rp.ReportRejected, match=match):
        rp.resolve(rp.parse_outline({"title": "t", "sections": [{"heading": "h", "blocks": [block]}]}), _arts())


def test_fmt_value():
    assert rp.fmt_value(1234567) == "1,234,567" and rp.fmt_value(0.5) == "0.5" and rp.fmt_value(None) == "—"
    assert rp.fmt_value(1e-7) == "1e-07" and rp.fmt_value(2.0) == "2" and rp.fmt_value(float("nan")) == "NaN"


def _resolved():
    return rp.resolve(rp.parse_outline(_outline()), _arts())


def test_docx():
    from docx import Document

    d = Document(io.BytesIO(rp.render_docx(_resolved())))
    text = "\n".join(p.text for p in d.paragraphs)
    cells = [c.text for t in d.tables for row in t.rows for c in row.cells]
    assert "9월 보고서" in text and INJECT in text and "1,234,567" in cells and INJECT in cells
    assert len(d.inline_shapes) == 1 and any("select 2" in c for c in cells)


def test_pptx():
    from pptx import Presentation

    prs = Presentation(io.BytesIO(rp.render_pptx(_resolved())))
    texts = [sh.text_frame.text for s in prs.slides for sh in s.shapes if sh.has_text_frame]
    cells = [c.text for s in prs.slides for sh in s.shapes if sh.has_table for row in sh.table.rows for c in row.cells]
    assert "9월 보고서" in texts and "1,234,567" in texts and INJECT in cells
    assert any(sh.shape_type == 13 for s in prs.slides for sh in s.shapes)  # picture


def test_pdf_renders_untrusted_text_literally():
    import pypdf

    pdf = rp.render_pdf(_resolved())
    assert pdf[:5] == b"%PDF-"
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    compact = text.replace(" ", "").replace("\n", "")
    assert "1,234,567" in text and '#read("/etc/passwd")' in compact and "@preview/cetz" in compact
    assert "root:" not in text  # nothing from the host filesystem


def _logo(fmt="PNG", size=(120, 40)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (26, 115, 232)).save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode()


def test_brand_from_record_rules():
    b = rp.brand_from({"primary_color": "#1a73e8", "logo_base64": _logo(), "report_footer": "Acme · Confidential"})
    assert b.accent == b.strong == "1A73E8" and b.logo_png[:4] == b"\x89PNG" and b.footer == "Acme · Confidential"
    light = rp.brand_from({"primary_color": "#FFEE00"})
    assert light.accent == "FFEE00" and light.strong == rp.UI  # too light for text: decoration only
    assert rp.brand_from({"primary_color": "red; x"}).accent == rp.BRAND  # invalid ignored
    assert rp.brand_from({"logo_base64": _logo("GIF")}).logo_png is None  # only PNG/JPEG
    assert rp.brand_from({"logo_base64": _logo(size=(3000, 3000))}).logo_png is None  # pixel cap (decompression bombs)
    assert rp.brand_from({"logo_base64": "not base64!!"}).logo_png is None
    assert rp.brand_from(None) == rp.Brand()


def test_branded_reports_in_all_formats():
    from docx import Document
    from pptx import Presentation

    brand = rp.brand_from({"primary_color": "#1A73E8", "logo_base64": _logo(), "report_footer": "Acme · Confidential"})
    r = rp.resolve(rp.parse_outline(_outline()), _arts(), brand)
    d = Document(io.BytesIO(rp.render_docx(r)))
    assert len(d.inline_shapes) == 2 and d.sections[0].footer.paragraphs[0].text == "Acme · Confidential"  # logo + chart
    prs = Presentation(io.BytesIO(rp.render_pptx(r)))
    first = prs.slides[0]
    assert any(sh.shape_type == 13 for sh in first.shapes)  # logo on the title slide
    import pypdf

    pdf = rp.render_pdf(r)
    text = " ".join(p.extract_text() for p in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    assert "Acme · Confidential" in text
    import os

    if os.environ.get("REPORT_OUT"):
        open(os.environ["REPORT_OUT"], "wb").write(pdf)
