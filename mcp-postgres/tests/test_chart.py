"""Chart spec whitelist, Vega-Lite builder and in-process rendering."""

import pytest

from mcpcluster_common import chart as ch

ROWS = [{"kind": "버그", "n": 3, "day": "2026-09-01"}, {"kind": "a.b[0]", "n": 5, "day": "2026-09-02"}]


def test_spec_whitelist_rejects_raw_vegalite_and_unknown_keys():
    for bad in (
        {"type": "bar", "x": {"field": "kind"}, "y": {"field": "n"}, "data": {"url": "file:///etc/passwd"}},
        {"type": "bar", "x": {"field": "kind", "url": "http://169.254.169.254/"}, "y": {"field": "n"}},
        {"type": "image", "x": {"field": "kind"}, "y": {"field": "n"}},
        {"type": "bar", "x": {"field": "kind"}},                       # y missing
        {"type": "histogram"},                                          # column missing
        {"type": "heatmap", "x": {"field": "kind"}, "y": {"field": "n"}},  # color missing
        {"type": "bar", "x": {"field": "k\x00"}, "y": {"field": "n"}},
        {"type": "bar", "x": {"field": "kind"}, "y": {"field": "n"}, "width": 10_000},
    ):
        with pytest.raises(ch.ChartRejected):
            ch.parse_chart(bad)


def test_builder_inlines_data_escapes_fields_and_emits_no_urls():
    spec = ch.parse_chart({"type": "bar", "x": {"field": "a.b[0]"}, "y": {"field": "n"}, "title": "제보 현황", "sort": "-y"})
    vl = ch.build_vegalite(spec, [{"a.b[0]": "x", "n": 1}])
    assert vl["data"] == {"values": [{"a.b[0]": "x", "n": 1}]}
    assert vl["encoding"]["x"]["field"] == "a\\.b\\[0\\]" and vl["encoding"]["x"]["sort"] == "-y"
    assert vl["encoding"]["y"]["type"] == "quantitative" and vl["encoding"]["x"]["type"] == "nominal"
    ch.assert_no_external_refs(vl)


def test_type_inference():
    spec = ch.parse_chart({"type": "line", "x": {"field": "day"}, "y": {"field": "n"}})
    vl = ch.build_vegalite(spec, ROWS)
    assert vl["encoding"]["x"]["type"] == "temporal"


def test_guard_catches_injected_external_refs():
    base = ch.build_vegalite(ch.parse_chart({"type": "bar", "x": {"field": "kind"}, "y": {"field": "n"}}), ROWS)
    for poison in ({"data": {"url": "file:///etc/passwd"}}, {"mark": {"type": "image", "href": "http://x"}},
                   {"transform": [{"calculate": "1", "as": "x"}]}, {"data": {"values": [], "url": "x"}}):
        with pytest.raises(ch.ChartRejected):
            ch.assert_no_external_refs({**base, **poison})
    # a data VALUE that looks like a URL is just data
    ch.assert_no_external_refs({**base, "data": {"values": [{"url": "file:///etc/passwd", "n": 1}]}})


def test_unknown_field_rejected():
    with pytest.raises(ch.ChartRejected, match="not in the data"):
        ch.build_vegalite(ch.parse_chart({"type": "bar", "x": {"field": "nope"}, "y": {"field": "n"}}), ROWS)


def test_histogram_rows_from_rust_kernel_output():
    assert ch.histogram_rows({"edges": [0, 1, 2], "counts": [4, 1]}) == [
        {"bin_start": 0, "bin_end": 1, "count": 4}, {"bin_start": 1, "bin_end": 2, "count": 1}]
    with pytest.raises(ch.ChartRejected):
        ch.histogram_rows({"edges": [0, 1], "counts": [1, 2]})


@pytest.mark.parametrize("kind", ["bar", "line", "area", "scatter"])
def test_render_png(kind):
    vl, png = ch.render(ch.parse_chart({"type": kind, "x": {"field": "kind"}, "y": {"field": "n"}, "title": "한글 제목"}), ROWS)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 1000 and vl["title"] == "한글 제목"


def test_render_histogram_and_heatmap():
    _, png = ch.render(ch.parse_chart({"type": "histogram", "column": "n"}), ch.histogram_rows({"edges": [0, 1, 2], "counts": [4, 1]}))
    assert png[:4] == b"\x89PNG"
    _, png = ch.render(ch.parse_chart({"type": "heatmap", "x": {"field": "kind"}, "y": {"field": "day"}, "color": {"field": "n"}}), ROWS)
    assert png[:4] == b"\x89PNG"


def test_ftg_theme_applied():
    vl = ch.build_vegalite(ch.parse_chart({"type": "bar", "x": {"field": "kind"}, "y": {"field": "n"}}), ROWS)
    cfg = vl["config"]
    assert cfg["range"]["category"][0] == "#00A758" and cfg["mark"]["color"] == "#00A758"
    assert len(set(ch.CATEGORICAL)) == len(ch.CATEGORICAL) == 7  # fixed order, never cycled/generated
    assert cfg["bar"]["cornerRadiusEnd"] == 4 and cfg["axisBand"]["labelAngle"] == 0
    ch.assert_no_external_refs(vl)  # the theme adds no external references
