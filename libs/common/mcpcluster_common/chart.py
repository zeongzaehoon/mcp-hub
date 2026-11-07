"""Charts from a whitelisted spec. The model never supplies Vega/Vega-Lite: we build the Vega-Lite spec
ourselves from a small pydantic model, with the data inlined, and render it in-process with vl-convert
(Rust + embedded JS engine, no browser).

Why no raw Vega-Lite: vl-convert's allowed_base_urls blocks http(s) data URLs but a `file:///` data URL
and image-mark hrefs still load (verified with vl-convert 1.9). A spec we build never contains a URL,
and `assert_no_external_refs` re-checks that before rendering (defence in depth).

Numbers are not computed by the chart: rows are plotted as given (aggregate first with compute /
transform); histograms use the Rust histogram kernel."""

import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChartType = Literal["bar", "line", "area", "scatter", "histogram", "heatmap"]
FieldType = Literal["quantitative", "temporal", "nominal", "ordinal"]
FONT_DIRS = ("/usr/share/fonts/truetype/nanum", "/usr/share/fonts/opentype/noto")
FONT_FAMILY = os.environ.get("CHART_FONT", "NanumGothic")
# FTG categorical order, led by the brand UI green. Validated with the dataviz palette checks (light surface):
# adjacent CVD ΔE >= 15.3, normal-vision ΔE >= 19.6. Slots 5-6 are < 3:1 on white, so colour is never the only
# carrier: charts keep a legend and reports ship the numbers as tables. Scatter (all-pairs) should stay <= 3 series.
CATEGORICAL = ["#00A758", "#2a78d6", "#eb6834", "#4a3aa7", "#e87ba4", "#eda100", "#e34948"]
SEQUENTIAL = ["#e6f6ed", "#7fd3a6", "#00A758", "#00733c", "#00401f"]  # one hue, light -> dark (heatmap)
TEXT, TEXT_MUTED, GRID = "#0A0A0A", "#737373", "#E5E5E5"
_FORBIDDEN_KEYS = {"url", "href", "$ref", "loader", "signals", "expr", "calculate", "filter", "transform"}


class ChartRejected(ValueError):
    pass


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=128)
    type: FieldType | None = None  # inferred from the data when omitted
    title: str | None = Field(default=None, max_length=120)

    @field_validator("field")
    @classmethod
    def _no_control(cls, v: str) -> str:
        if re.search(r"[\x00-\x1f]", v):
            raise ValueError("field names must not contain control characters")
        return v


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: ChartType
    x: Channel | None = None
    y: Channel | None = None
    color: Channel | None = None
    column: str | None = Field(default=None, max_length=128)  # histogram: numeric column
    bins: int = Field(default=20, ge=1, le=200)  # histogram
    title: str | None = Field(default=None, max_length=200)
    sort: Literal["none", "x", "-x", "y", "-y"] = "none"
    width: int = Field(default=640, ge=100, le=1600)
    height: int = Field(default=360, ge=100, le=1200)

    @model_validator(mode="after")
    def _channels(self) -> "ChartSpec":
        if self.type == "histogram":
            if not self.column:
                raise ValueError("histogram needs `column`")
        elif self.x is None or self.y is None:
            raise ValueError(f"{self.type} needs `x` and `y`")
        if self.type == "heatmap" and self.color is None:
            raise ValueError("heatmap needs `color` (the value encoded as colour)")
        return self


def parse_chart(raw: Any) -> ChartSpec:
    try:
        return ChartSpec.model_validate(raw)
    except Exception as e:  # noqa: BLE001
        raise ChartRejected(f"invalid chart spec: {e}") from e


def _escape_field(name: str) -> str:
    # Vega-Lite treats `.` and `[ ]` in a field as nested access; the column name is literal
    return re.sub(r"([.\[\]\\])", r"\\\1", name)


def _infer(rows: list[dict[str, Any]], field: str) -> FieldType:
    vals = [r.get(field) for r in rows[:200] if r.get(field) is not None]
    if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "quantitative"
    if vals and all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?", v) for v in vals):
        return "temporal"
    return "nominal"


def _enc(ch: Channel, rows: list[dict[str, Any]], sort: str | None = None) -> dict[str, Any]:
    if rows and all(ch.field not in r for r in rows[:50]):
        raise ChartRejected(f"field '{ch.field}' is not in the data")
    e: dict[str, Any] = {"field": _escape_field(ch.field), "type": ch.type or _infer(rows, ch.field), "title": ch.title or ch.field}
    if sort:
        e["sort"] = sort
    return e


def histogram_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Rust histogram kernel output {edges, counts} -> plottable rows."""
    edges, counts = result.get("edges") or [], result.get("counts") or []
    if len(edges) != len(counts) + 1:
        raise ChartRejected("histogram result is malformed")
    return [{"bin_start": edges[i], "bin_end": edges[i + 1], "count": counts[i]} for i in range(len(counts))]


def build_vegalite(spec: ChartSpec, rows: list[dict[str, Any]], *, font: str | None = None) -> dict[str, Any]:
    vl: dict[str, Any] = {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "width": spec.width, "height": spec.height,
        "data": {"values": rows},
        "background": "#FFFFFF",
        "config": {
            "view": {"stroke": None},
            "range": {"category": CATEGORICAL, "heatmap": SEQUENTIAL, "ramp": SEQUENTIAL},
            "mark": {"color": CATEGORICAL[0]},
            "bar": {"cornerRadiusEnd": 4, "binSpacing": 2},
            "line": {"strokeWidth": 2},
            "point": {"size": 64, "filled": True},
            "rect": {"stroke": "#FFFFFF", "strokeWidth": 2},
            "axis": {"gridColor": GRID, "domainColor": GRID, "tickColor": GRID, "labelColor": TEXT_MUTED, "titleColor": TEXT_MUTED, "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": "normal"},
            "axisX": {"grid": False},
            "axisBand": {"labelAngle": 0, "labelOverlap": "greedy", "labelLimit": 120},  # horizontal Korean labels
            "scale": {"bandPaddingInner": 0.35, "bandPaddingOuter": 0.2},  # thin bars
            "legend": {"labelColor": TEXT, "titleColor": TEXT_MUTED, "orient": "top"},
            "title": {"color": TEXT, "fontSize": 14, "anchor": "start"},
            **({"font": font} if font else {}),
        },
    }
    if spec.title:
        vl["title"] = spec.title
    if spec.type == "histogram":
        vl["mark"] = {"type": "bar", "tooltip": True}
        vl["encoding"] = {
            "x": {"field": "bin_start", "type": "quantitative", "bin": {"binned": True}, "title": spec.column},
            "x2": {"field": "bin_end"},
            "y": {"field": "count", "type": "quantitative", "title": "count"},
        }
        return vl
    sort_map = {"none": None, "x": "x", "-x": "-x", "y": "y", "-y": "-y"}
    x_sort = sort_map[spec.sort] if spec.sort in ("y", "-y") else None
    enc: dict[str, Any] = {"x": _enc(spec.x, rows, x_sort), "y": _enc(spec.y, rows)}  # type: ignore[arg-type]
    if spec.sort in ("x", "-x"):
        enc["x"]["sort"] = "ascending" if spec.sort == "x" else "descending"
    if spec.color is not None:
        enc["color"] = _enc(spec.color, rows)
    enc["tooltip"] = [{"field": e["field"], "type": e["type"], "title": e["title"]} for k, e in enc.items() if k in ("x", "y", "color")]
    mark = {"bar": "bar", "line": "line", "area": "area", "scatter": "point", "heatmap": "rect"}[spec.type]
    vl["mark"] = {"type": mark, **({"point": True} if mark == "line" else {})}
    vl["encoding"] = enc
    return vl


def assert_no_external_refs(vl: dict[str, Any]) -> None:
    """Our builder never emits these; refuse to render if one ever appears outside inline data."""

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if path == "data" and k == "values":
                    continue  # inline data: values are data, never fetched
                if k in _FORBIDDEN_KEYS:
                    raise ChartRejected(f"refusing to render: '{k}' at {path or '<root>'}")
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for v in node:
                walk(v, path)

    walk(vl, "")
    if vl.get("data", {}).keys() - {"values"}:
        raise ChartRejected("refusing to render: data must be inline values only")


_font_ready: str | None | bool = False


def _font() -> str | None:
    global _font_ready
    if _font_ready is False:
        import vl_convert as vlc

        found = None
        for d in FONT_DIRS:
            if os.path.isdir(d):
                vlc.register_font_directory(d)
                found = FONT_FAMILY
        _font_ready = found
    return _font_ready or None


def render(spec: ChartSpec, rows: list[dict[str, Any]], *, fmt: Literal["png", "svg"] = "png") -> tuple[dict[str, Any], bytes]:
    import vl_convert as vlc

    vl = build_vegalite(spec, rows, font=_font())
    assert_no_external_refs(vl)
    if fmt == "svg":
        return vl, vlc.vegalite_to_svg(vl, allowed_base_urls=[]).encode()
    return vl, vlc.vegalite_to_png(vl, scale=2, allowed_base_urls=[])
