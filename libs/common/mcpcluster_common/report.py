"""Progress reports as DOCX / PPTX / PDF from an outline whose figures come only from artifacts.

The outline is a whitelisted pydantic model: headings, text, and blocks that *reference* artifacts
(kpi, table, chart). Numbers in kpi/table blocks are read from the stored artifact, never from the model,
and an appendix lists every artifact used (server, time, source query) so the report is auditable.

Renderers are plain libraries (python-docx, python-pptx, typst) — no browser, no office runtime.
Typst gets the content as a JSON data file that the (server-owned) template reads with `json()`: strings
are rendered as text, never evaluated as markup, and the compile root is a private temp directory."""

import base64
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReportFormat = Literal["docx", "pptx", "pdf"]
CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
}
FONT = os.environ.get("REPORT_FONT", "NanumGothic")
FONT_DIRS = ("/usr/share/fonts/truetype/nanum",)
# FindTheGap design tokens (DESIGN.md): brand green for accents, ui green for fills, neutral text
BRAND, UI, FG, MUTED, BORDER = "3CDC84", "00A758", "0A0A0A", "737373", "E5E5E5"
MAX_BLOCKS = 120
MAX_ARTIFACTS = 20
MAX_TOTAL_BYTES = 20_000_000
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 12


class ReportRejected(ValueError):
    pass


LOGO_MAX_PIXELS = 4_000_000


@dataclass
class Brand:
    """Company branding for reports. `accent` decorates (bars, rules); `strong` is used for text and for fills that
    carry white text, so it falls back to FTG green when the brand colour is too light to read (< 3:1 on white)."""

    accent: str = BRAND
    strong: str = UI
    logo_png: bytes | None = None
    footer: str | None = None


def _contrast_on_white(hex6: str) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (0, 2, 4))
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return 1.05 / (lum + 0.05)


def brand_from(record: dict[str, Any] | None) -> Brand:
    """Build a Brand from the `branding:{company}` record; anything invalid is ignored (default FTG theme)."""
    if not record:
        return Brand()
    b = Brand(footer=(str(record.get("report_footer") or "")[:200] or None))
    color = str(record.get("primary_color") or "").lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        b.accent = color.upper()
        if _contrast_on_white(color) >= 3.0:
            b.strong = color.upper()
    if record.get("logo_base64"):
        try:
            from PIL import Image

            raw = base64.b64decode(record["logo_base64"], validate=True)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ("PNG", "JPEG") or im.width * im.height > LOGO_MAX_PIXELS:
                    raise ValueError("unsupported or oversized logo")
                im.thumbnail((800, 800))
                out = io.BytesIO()
                im.convert("RGBA").save(out, format="PNG")  # normalised: renderers only ever see a clean PNG
                b.logo_png = out.getvalue()
        except Exception:  # noqa: BLE001 — a broken logo never breaks the report
            b.logo_png = None
    return b


class _B(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextBlock(_B):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=5000)


class KpiBlock(_B):
    type: Literal["kpi"]
    artifact_id: str
    fields: list[str] | None = Field(default=None, max_length=8)  # keys of the compute result; default: numeric ones
    labels: dict[str, str] | None = None  # display names for fields
    caption: str | None = Field(default=None, max_length=300)


class TableBlock(_B):
    type: Literal["table"]
    artifact_id: str
    columns: list[str] | None = Field(default=None, max_length=MAX_TABLE_COLS)
    max_rows: int = Field(default=50, ge=1, le=MAX_TABLE_ROWS)
    caption: str | None = Field(default=None, max_length=300)


class ChartBlock(_B):
    type: Literal["chart"]
    artifact_id: str
    caption: str | None = Field(default=None, max_length=300)


Block = Annotated[TextBlock | KpiBlock | TableBlock | ChartBlock, Field(discriminator="type")]


class Section(_B):
    heading: str = Field(min_length=1, max_length=200)
    blocks: list[Block] = Field(default_factory=list, max_length=40)


class Outline(_B):
    title: str = Field(min_length=1, max_length=200)
    subtitle: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=120)
    sections: list[Section] = Field(min_length=1, max_length=30)


def parse_outline(raw: Any) -> Outline:
    try:
        o = Outline.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        raise ReportRejected(f"invalid outline: {e}") from e
    if sum(len(s.blocks) for s in o.sections) > MAX_BLOCKS:
        raise ReportRejected(f"at most {MAX_BLOCKS} blocks per report")
    return o


def artifact_ids(o: Outline) -> list[str]:
    seen: dict[str, None] = {}
    for s in o.sections:
        for b in s.blocks:
            if not isinstance(b, TextBlock):
                seen[b.artifact_id] = None
    return list(seen)


# ---- resolved content (renderer input) ----------------------------------------------------------
@dataclass
class RKpi:
    items: list[tuple[str, str]]
    caption: str | None


@dataclass
class RTable:
    columns: list[str]
    rows: list[list[str]]
    truncated_from: int | None
    caption: str | None


@dataclass
class RChart:
    png: bytes
    caption: str | None


@dataclass
class RText:
    paragraphs: list[tuple[str, bool]]  # (text, is_bullet)


@dataclass
class RSection:
    heading: str
    blocks: list[Any] = field(default_factory=list)


@dataclass
class Resolved:
    title: str
    subtitle: str | None
    author: str | None
    generated_at: str
    sections: list[RSection]
    sources: list[dict[str, str]]
    brand: Brand = field(default_factory=Brand)


def fmt_value(v: Any) -> str:
    """Display formatting only; the digits are the artifact's."""
    if isinstance(v, bool) or v is None:
        return "—" if v is None else str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        if v != v:  # NaN
            return "NaN"
        if abs(v) >= 1e15 or (v != 0 and abs(v) < 1e-4):
            return f"{v:.6g}"
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)[:200]
    return str(v)[:500]


def _text(t: str) -> RText:
    out: list[tuple[str, bool]] = []
    for para in re.split(r"\n\s*\n", t.strip()):
        lines = [ln.rstrip() for ln in para.splitlines() if ln.strip()]
        if lines and all(ln.lstrip().startswith(("- ", "* ", "• ")) for ln in lines):
            out += [(ln.lstrip()[2:].strip(), True) for ln in lines]
        elif lines:
            out.append((" ".join(ln.strip() for ln in lines), False))
    return RText(out)


def _rows_of(kind: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("rows"), list):
        return [r for r in payload["rows"] if isinstance(r, dict)]
    result = payload.get("result")
    if payload.get("op") == "histogram" and isinstance(result, dict):
        e, c = result.get("edges") or [], result.get("counts") or []
        return [{"bin_start": e[i], "bin_end": e[i + 1], "count": c[i]} for i in range(min(len(c), len(e) - 1))]
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    raise ReportRejected(f"{kind} artifact has no rows for a table")


def resolve(o: Outline, artifacts: dict[str, tuple[Any, dict[str, Any]]], brand: Brand | None = None) -> Resolved:
    """artifacts: artifact_id -> (ArtifactMeta, payload), already ownership/authorization-checked."""
    sections: list[RSection] = []
    for s in o.sections:
        rs = RSection(s.heading)
        for b in s.blocks:
            if isinstance(b, TextBlock):
                rs.blocks.append(_text(b.text))
                continue
            meta, payload = artifacts[b.artifact_id]
            if isinstance(b, ChartBlock):
                if meta.kind != "chart" or not payload.get("png_base64"):
                    raise ReportRejected(f"{b.artifact_id} is not a chart artifact")
                rs.blocks.append(RChart(base64.b64decode(payload["png_base64"]), b.caption or meta.title or None))
            elif isinstance(b, KpiBlock):
                result = payload.get("result")
                if meta.kind != "compute" or not isinstance(result, dict):
                    raise ReportRejected(f"{b.artifact_id}: kpi needs a compute artifact with a single-record result (e.g. describe)")
                keys = b.fields or [k for k, v in result.items() if isinstance(v, (int, float)) and not isinstance(v, bool)][:6]
                missing = [k for k in keys if k not in result]
                if missing:
                    raise ReportRejected(f"{b.artifact_id}: fields not in the result: {missing}")
                labels = b.labels or {}
                rs.blocks.append(RKpi([(labels.get(k, k), fmt_value(result[k])) for k in keys], b.caption))
            else:  # TableBlock
                rows = _rows_of(meta.kind, payload)
                cols = b.columns or list(dict.fromkeys(k for r in rows[:50] for k in r))[:MAX_TABLE_COLS]
                missing = [c for c in cols if rows and all(c not in r for r in rows[:50])]
                if missing:
                    raise ReportRejected(f"{b.artifact_id}: columns not in the rows: {missing}")
                shown = rows[: b.max_rows]
                rs.blocks.append(RTable(cols, [[fmt_value(r.get(c)) for c in cols] for r in shown],
                                        len(rows) if len(rows) > len(shown) else None, b.caption))
        sections.append(rs)
    sources = []
    for aid, (meta, payload) in artifacts.items():
        src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        query = src.get("sql") or (json.dumps({k: src[k] for k in ("collection", "filter", "pipeline") if k in src}, ensure_ascii=False) if src else "")
        sources.append({"artifact_id": aid, "title": meta.title, "kind": meta.kind, "server": f"{meta.server_type} {meta.server_id}",
                        "created_at": meta.created_at[:19].replace("T", " ") + " UTC", "query": (query or "")[:500]})
    return Resolved(o.title, o.subtitle, o.author, datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), sections, sources, brand or Brand())


# ---- DOCX ---------------------------------------------------------------------------------------
def render_docx(r: Resolved) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()
    for sname in ("Normal", "Title", "Subtitle", "Heading 1", "Heading 2", "Caption"):
        st = doc.styles[sname]
        st.font.name = FONT
        st.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT)
    doc.styles["Normal"].font.size = Pt(10.5)
    for sname in ("Title", "Heading 1", "Heading 2"):
        doc.styles[sname].font.color.rgb = RGBColor.from_string(FG)

    for sname in ("Title", "Heading 1"):
        doc.styles[sname].font.color.rgb = RGBColor.from_string(r.brand.strong)
    if r.brand.logo_png:
        doc.add_picture(io.BytesIO(r.brand.logo_png), width=Inches(1.4))
    if r.brand.footer:
        doc.sections[0].footer.paragraphs[0].text = r.brand.footer
    doc.add_paragraph(r.title, style="Title")
    if r.subtitle:
        doc.add_paragraph(r.subtitle, style="Subtitle")
    meta = doc.add_paragraph(" · ".join(x for x in (r.author, r.generated_at) if x))
    meta.runs[0].font.color.rgb = RGBColor.from_string(MUTED)

    def caption(t: str | None) -> None:
        if t:
            p = doc.add_paragraph(t, style="Caption")
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def table(cols: list[str], rows: list[list[str]]) -> None:
        t = doc.add_table(rows=1, cols=len(cols))
        t.style = "Table Grid"
        for i, c in enumerate(cols):
            cell = t.rows[0].cells[i]
            cell.text = c
            cell.paragraphs[0].runs[0].font.bold = True
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = v

    for s in r.sections:
        doc.add_heading(s.heading, level=1)
        for b in s.blocks:
            if isinstance(b, RText):
                for text, bullet in b.paragraphs:
                    doc.add_paragraph(text, style="List Bullet" if bullet else None)
            elif isinstance(b, RKpi):
                table([k for k, _ in b.items], [[v for _, v in b.items]])
                caption(b.caption)
            elif isinstance(b, RTable):
                table(b.columns, b.rows)
                caption((b.caption or "") + (f" (상위 {len(b.rows)}행 / 전체 {b.truncated_from}행)" if b.truncated_from else ""))
            elif isinstance(b, RChart):
                doc.add_picture(io.BytesIO(b.png), width=Inches(6.2))
                caption(b.caption)
    doc.add_heading("부록: 데이터 출처", level=1)
    table(["산출물", "종류", "서버", "생성 시각", "쿼리"], [[f"{x['title']} ({x['artifact_id']})", x["kind"], x["server"], x["created_at"], x["query"]] for x in r.sources])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---- PPTX ---------------------------------------------------------------------------------------
def render_pptx(r: Resolved) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W, H = prs.slide_width, prs.slide_height

    def text_box(slide, x, y, w, h, text, size=14, bold=False, color=FG):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = text
        run.font.size, run.font.bold, run.font.name = Pt(size), bold, FONT
        run.font.color.rgb = RGBColor.from_string(color)
        return tf

    def slide_with_title(title: str):
        s = prs.slides.add_slide(blank)
        bar = s.shapes.add_shape(1, 0, 0, W, Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = RGBColor.from_string(r.brand.accent)
        bar.line.fill.background()
        text_box(s, Inches(0.6), Inches(0.35), W - Inches(1.2), Inches(0.8), title, size=26, bold=True)
        return s

    def grid(s, cols, rows, top):
        n_rows = len(rows) + 1
        shape = s.shapes.add_table(n_rows, len(cols), Inches(0.6), top, W - Inches(1.2), Emu(min(int(Inches(0.4)) * n_rows, int(H - top - Inches(0.5)))))
        tbl = shape.table
        for i, c in enumerate(cols):
            cell = tbl.cell(0, i)
            cell.text = c
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(r.brand.strong)
        for ri, row in enumerate(rows, start=1):
            for ci, v in enumerate(row):
                tbl.cell(ri, ci).text = v
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.text_frame.paragraphs:
                    for run in p.runs:
                        run.font.size, run.font.name = Pt(11), FONT
        for i in range(len(cols)):
            for run in tbl.cell(0, i).text_frame.paragraphs[0].runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor.from_string("FFFFFF")

    s = prs.slides.add_slide(blank)
    if r.brand.logo_png:
        s.shapes.add_picture(io.BytesIO(r.brand.logo_png), Inches(0.8), Inches(0.8), height=Inches(1.0))
    text_box(s, Inches(0.8), Inches(2.6), W - Inches(1.6), Inches(1.2), r.title, size=40, bold=True)
    if r.subtitle:
        text_box(s, Inches(0.8), Inches(3.8), W - Inches(1.6), Inches(0.8), r.subtitle, size=20, color=MUTED)
    text_box(s, Inches(0.8), Inches(6.3), W - Inches(1.6), Inches(0.5), " · ".join(x for x in (r.author, r.generated_at, r.brand.footer) if x), size=12, color=MUTED)

    per_slide = 12
    for sec in r.sections:
        texts = [b for b in sec.blocks if isinstance(b, RText)]
        if texts or not sec.blocks:
            sl = slide_with_title(sec.heading)
            tf = text_box(sl, Inches(0.8), Inches(1.4), W - Inches(1.6), H - Inches(2), "", size=16)
            first = True
            for b in texts:
                for text, bullet in b.paragraphs:
                    p = tf.paragraphs[0] if first else tf.add_paragraph()
                    first = False
                    run = p.add_run()
                    run.text = ("• " if bullet else "") + text
                    run.font.size, run.font.name = Pt(16), FONT
        for b in sec.blocks:
            if isinstance(b, RKpi):
                sl = slide_with_title(sec.heading)
                n = len(b.items)
                bw = (W - Inches(1.2) - Inches(0.3) * (n - 1)) // max(n, 1)
                for i, (label, value) in enumerate(b.items):
                    x = Inches(0.6) + (bw + Inches(0.3)) * i
                    box = sl.shapes.add_shape(1, x, Inches(2.2), bw, Inches(2.4))
                    box.fill.solid()
                    box.fill.fore_color.rgb = RGBColor.from_string("F5F5F5")
                    box.line.color.rgb = RGBColor.from_string(BORDER)
                    text_box(sl, x + Inches(0.2), Inches(2.4), bw - Inches(0.4), Inches(0.6), label, size=14, color=MUTED)
                    text_box(sl, x + Inches(0.2), Inches(3.1), bw - Inches(0.4), Inches(1.2), value, size=32, bold=True, color=r.brand.strong)
                if b.caption:
                    text_box(sl, Inches(0.6), Inches(5.0), W - Inches(1.2), Inches(0.6), b.caption, size=12, color=MUTED)
            elif isinstance(b, RTable):
                for start in range(0, max(len(b.rows), 1), per_slide):
                    sl = slide_with_title(sec.heading + (f" ({start // per_slide + 1})" if len(b.rows) > per_slide else ""))
                    grid(sl, b.columns, b.rows[start:start + per_slide], Inches(1.4))
                note = (b.caption or "") + (f" (상위 {len(b.rows)}행 / 전체 {b.truncated_from}행)" if b.truncated_from else "")
                if note.strip():
                    text_box(sl, Inches(0.6), H - Inches(0.6), W - Inches(1.2), Inches(0.4), note, size=11, color=MUTED)
            elif isinstance(b, RChart):
                sl = slide_with_title(sec.heading)
                pic = sl.shapes.add_picture(io.BytesIO(b.png), Inches(0.6), Inches(1.3), height=H - Inches(2.2))
                if pic.width > W - Inches(1.2):  # keep aspect, fit width
                    ratio = (W - Inches(1.2)) / pic.width
                    pic.width, pic.height = int(pic.width * ratio), int(pic.height * ratio)
                pic.left = int((W - pic.width) / 2)
                if b.caption:
                    text_box(sl, Inches(0.6), H - Inches(0.7), W - Inches(1.2), Inches(0.4), b.caption, size=12, color=MUTED)
    rows = [[x["title"], x["kind"], x["server"], x["created_at"], x["query"][:120]] for x in r.sources]
    for start in range(0, max(len(rows), 1), per_slide):
        grid(slide_with_title("부록: 데이터 출처"), ["산출물", "종류", "서버", "생성 시각", "쿼리"], rows[start:start + per_slide], Inches(1.4))
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---- PDF (typst) --------------------------------------------------------------------------------
_TYPST_TEMPLATE = r"""
#let d = json("data.json")
#set document(title: d.title)
#set page(paper: "a4", margin: (x: 2cm, y: 2.2cm), footer: context [#text(8pt, fill: rgb("#MUTED"), if d.footer != none { d.footer } else { "" }) #h(1fr) #text(8pt, fill: rgb("#MUTED"))[#counter(page).display()]])
#set text(font: "FONT", size: 10pt, fill: rgb("#FG"), lang: "ko")
#set par(justify: false, leading: 0.7em)
#show heading.where(level: 1): it => block(above: 1.4em, below: 0.8em)[
  #box(width: 4pt, height: 1em, fill: rgb("#BRAND")) #h(6pt) #text(15pt, weight: "bold", it.body)
]
#let grid-table(cols, rows) = table(
  columns: cols.len(), stroke: 0.5pt + rgb("#BORDER"), inset: 5pt,
  fill: (x, y) => if y == 0 { rgb("#UI") },
  ..cols.map(c => text(fill: white, weight: "bold", size: 9pt, c)),
  ..rows.flatten().map(v => text(size: 9pt, v)),
)
#let caption(c) = if c != none and c != "" { block(above: 4pt, text(8.5pt, fill: rgb("#MUTED"), c)) }

#if d.logo != none { image(d.logo, height: 1.2cm); v(6pt) }
#text(22pt, weight: "bold", fill: rgb("#UI"), d.title)
#if d.subtitle != none { linebreak(); text(13pt, fill: rgb("#MUTED"), d.subtitle) }
#v(4pt)
#text(9pt, fill: rgb("#MUTED"), d.meta)
#line(length: 100%, stroke: 1.5pt + rgb("#BRAND"))

#for s in d.sections [
  = #s.heading
  #for b in s.blocks {
    if b.kind == "text" {
      for p in b.paragraphs {
        if p.bullet { list(p.text) } else { par(p.text) }
      }
    } else if b.kind == "kpi" {
      grid(columns: b.items.len(), gutter: 8pt, ..b.items.map(it => block(
        fill: rgb("#F5F5F5"), stroke: 0.5pt + rgb("#BORDER"), inset: 10pt, radius: 4pt, width: 100%,
        [#text(8.5pt, fill: rgb("#MUTED"), it.label) \ #text(16pt, weight: "bold", fill: rgb("#UI"), it.value)])))
      caption(b.caption)
    } else if b.kind == "table" {
      grid-table(b.columns, b.rows)
      caption(b.caption)
    } else if b.kind == "chart" {
      image(b.file, width: 100%)
      caption(b.caption)
    }
  }
]

= 부록: 데이터 출처
#grid-table(("산출물", "종류", "서버", "생성 시각", "쿼리"), d.sources.map(x => (x.title + " (" + x.artifact_id + ")", x.kind, x.server, x.created_at, x.query)))
"""


def render_pdf(r: Resolved) -> bytes:
    import typst

    tpl = _TYPST_TEMPLATE.replace("FONT", FONT)
    for k, v in (("MUTED", MUTED), ("FG", FG), ("BRAND", r.brand.accent), ("BORDER", BORDER), ("UI", r.brand.strong)):
        tpl = tpl.replace(f"#{k}", f"#{v}")
    with tempfile.TemporaryDirectory(prefix="report-") as root, tempfile.TemporaryDirectory(prefix="typst-pkg-") as pkgs:
        sections, n = [], 0
        for s in r.sections:
            blocks = []
            for b in s.blocks:
                if isinstance(b, RText):
                    blocks.append({"kind": "text", "paragraphs": [{"text": t, "bullet": bl} for t, bl in b.paragraphs]})
                elif isinstance(b, RKpi):
                    blocks.append({"kind": "kpi", "items": [{"label": k, "value": v} for k, v in b.items], "caption": b.caption})
                elif isinstance(b, RTable):
                    note = (b.caption or "") + (f" (상위 {len(b.rows)}행 / 전체 {b.truncated_from}행)" if b.truncated_from else "")
                    blocks.append({"kind": "table", "columns": b.columns, "rows": b.rows, "caption": note})
                elif isinstance(b, RChart):
                    n += 1
                    fname = f"chart-{n}.png"
                    with open(os.path.join(root, fname), "wb") as f:
                        f.write(b.png)
                    blocks.append({"kind": "chart", "file": fname, "caption": b.caption})
            sections.append({"heading": s.heading, "blocks": blocks})
        logo = None
        if r.brand.logo_png:
            with open(os.path.join(root, "logo.png"), "wb") as f:
                f.write(r.brand.logo_png)
            logo = "logo.png"
        data = {"title": r.title, "subtitle": r.subtitle, "meta": " · ".join(x for x in (r.author, r.generated_at) if x),
                "sections": sections, "sources": r.sources, "logo": logo, "footer": r.brand.footer}
        with open(os.path.join(root, "data.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        main = os.path.join(root, "main.typ")
        with open(main, "w", encoding="utf-8") as f:
            f.write(tpl)
        fonts = [d for d in FONT_DIRS if os.path.isdir(d)]
        return typst.compile(main, root=root, font_paths=fonts, ignore_system_fonts=bool(fonts),
                             package_path=pkgs, package_cache_path=pkgs)


RENDERERS = {"docx": render_docx, "pptx": render_pptx, "pdf": render_pdf}
