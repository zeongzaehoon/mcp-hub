"""Engine-agnostic `transform`, `compute`, `export` and artifact tools. A server plugs in one coroutine
that turns its own `source` spec into rows; everything after that (cleaning, redaction, numerics, file
building, delivery, artifact storage) is shared.
"""

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent

import orjson

from mcpcluster_common import chart as ch
from mcpcluster_common import compute as cp
from mcpcluster_common import layout
from mcpcluster_common import monitor as mon
from mcpcluster_common import report as rp
from mcpcluster_common import transform as tf
from mcpcluster_common.artifacts import ArtifactNotFound, ArtifactRejected, ArtifactStore
from mcpcluster_common.authz import Resolver
from mcpcluster_common.compute_spec import ComputeRejected, Transfer, parse_compute, run_compute
from mcpcluster_common.context import current_tenant
from mcpcluster_common.export import Delivery, Exporter, ExportRejected, Format, safe_filename, to_csv_bytes, to_xlsx_bytes
from mcpcluster_common.postprocess import redact_rows, redact_value
from mcpcluster_common.settings import CommonSettings

log = logging.getLogger(__name__)

# rows (arrow-friendly scalars), meta (e.g. {"sql": "...", "truncated": bool})
SourceFetcher = Callable[[dict[str, Any], int], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]]]

TOOL_PERMISSIONS = {"transform": "read", "compute": "read", "export": "export", "chart": "read", "report": "export",
                    "list_artifacts": "read", "get_artifact": "read",
                    "monitor_create": "read", "monitor_list": "read", "monitor_status": "read", "monitor_pause": "read", "monitor_delete": "read"}
CHART_MAX_POINTS = 5000


def install_data_tools(mcp: FastMCP, fetch: SourceFetcher, settings: CommonSettings, *, source_doc: str, resolver: Resolver | None = None) -> ArtifactStore:
    exporter = Exporter(settings)
    resolver = resolver or Resolver(settings)
    from mcpcluster_common.metrics import install_metrics

    install_metrics(mcp)
    artifacts = ArtifactStore(settings, resolver.server_readable, storage=exporter.storage)

    async def _compute_for_monitor(source: dict[str, Any], pipeline: list[dict[str, Any]] | None, op: dict[str, Any]):
        """Same path as the compute tool (guarded source, pipeline, Rust kernels) for scheduled runs."""
        spec = parse_compute(op)
        rows, meta = await fetch(source, settings.monitor_max_rows)
        df = tf.apply(tf.build_frame(rows), tf.parse_pipeline(pipeline))
        result, _ = run_compute(df, spec, arrow_threshold=settings.compute_arrow_threshold_rows)
        return redact_value(result), meta

    monitors = mon.Monitors(settings, resolver, exporter.storage, _compute_for_monitor)
    mon._ACTIVE.append(monitors)

    async def _save(kind: str, title: str | None, payload: dict[str, Any], derived_from: tuple[str, ...] = ()) -> dict[str, Any]:
        """Saving puts data into shared storage: gated by `export`, like any other data egress."""
        ctx = current_tenant()
        if "export" not in ctx.permissions:
            raise ToolError("save requires the 'export' permission on this server")
        try:
            meta = await artifacts.put(ctx, kind=kind, title=title, payload=payload, derived_from=derived_from)
        except ArtifactRejected as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001 - storage problems must not leak credentials/hosts
            log.exception("artifact save failed")
            raise ToolError("artifact save failed: object storage unavailable") from e
        return meta.public()

    async def transform(source: dict[str, Any], pipeline: list[dict[str, Any]], limit: int = 1000, save: bool = False, title: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        ops = _pipeline(pipeline)
        rows, meta = await fetch(source, settings.export_max_rows)
        try:
            df = tf.apply(tf.build_frame(rows), ops)
        except tf.TransformRejected as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001 - polars errors carry the useful message
            raise ToolError(f"transform failed: {e}") from e
        cap = max(1, min(int(limit), 10_000))
        out = redact_rows(tf.frame_to_rows(df.head(cap)))
        columns = [{"name": c, "type": str(t)} for c, t in zip(df.columns, df.dtypes, strict=True)]
        res = {**meta, "input_rows": len(rows), "row_count": min(df.height, cap), "truncated": df.height > cap,
               "columns": columns, "rows": out}
        if save:
            res["artifact"] = await _save("table", title, {"columns": columns, "rows": out, "truncated": df.height > cap, "source": meta})
        res["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return res

    async def export(source: dict[str, Any], format: Format = "csv", pipeline: list[dict[str, Any]] | None = None,
                     delivery: Delivery = "auto", filename: str | None = None, csv_bom: bool = True) -> dict[str, Any]:
        started = time.perf_counter()
        ctx = current_tenant()
        ops = _pipeline(pipeline)
        rows, meta = await fetch(source, settings.export_max_rows)
        try:
            df = tf.apply(tf.build_frame(rows), ops)
            df = tf.redact_frame(df)  # same masking policy as query results
            data = to_csv_bytes(df, bom=csv_bom) if format == "csv" else to_xlsx_bytes(df)
        except (tf.TransformRejected, ExportRejected) as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"export failed: {e}") from e
        name = safe_filename(filename, format, f"{ctx.server_type}-export")
        try:
            delivered = await exporter.deliver(data, ctx=ctx, fmt=format, delivery=delivery, filename=name)
        except Exception as e:  # noqa: BLE001 - storage problems must not leak credentials/hosts
            log.exception("export delivery failed")
            raise ToolError("export delivery failed: object storage unavailable") from e
        return {**meta, "format": format, "rows": df.height, "columns": df.columns, **delivered.as_dict(),
                "duration_ms": int((time.perf_counter() - started) * 1000)}

    async def compute(source: dict[str, Any], op: dict[str, Any], pipeline: list[dict[str, Any]] | None = None, transfer: Transfer = "auto",
                      save: bool = False, title: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            spec = parse_compute(op)
        except ComputeRejected as e:
            raise ToolError(f"rejected: {e}") from e
        ops = _pipeline(pipeline)
        rows, meta = await fetch(source, settings.export_max_rows)
        try:
            df = tf.apply(tf.build_frame(rows), ops)
            result, used = run_compute(df, spec, transfer=transfer, arrow_threshold=settings.compute_arrow_threshold_rows)
            result = redact_value(result)  # group keys / top-k values are data too (numbers are untouched)
        except (tf.TransformRejected, ComputeRejected) as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001
            raise ToolError(f"compute failed: {e}") from e
        res = {**meta, "op": spec.op, "input_rows": df.height, "engine": cp.engine(), "transfer": used, "result": result}
        if save:
            res["artifact"] = await _save("compute", title, {"op": spec.op, "spec": op, "engine": cp.engine(), "input_rows": df.height, "result": result, "source": meta})
        res["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return res

    async def _artifact_rows(artifact_id: str, *, permission: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            meta, payload = await artifacts.get(current_tenant(), artifact_id, permission=permission)
        except ArtifactNotFound as e:
            raise ToolError("artifact not found") from e
        info = {"artifact_id": artifact_id, "artifact_server_id": meta.server_id, "sources": list(meta.sources or (meta.server_id,))}
        if isinstance(payload.get("rows"), list):
            return payload["rows"], info
        result = payload.get("result")
        if payload.get("op") == "histogram" and isinstance(result, dict):
            return ch.histogram_rows(result), {**info, "histogram": True}
        if isinstance(result, list) and all(isinstance(r, dict) for r in result):
            return result, info
        raise ToolError("rejected: this artifact has no plottable rows (use a table, or a compute result that is a list of rows or a histogram)")

    async def chart(spec: dict[str, Any], source: dict[str, Any] | None = None, pipeline: list[dict[str, Any]] | None = None,
                    artifact_id: str | None = None, save: bool = False, title: str | None = None) -> ToolResult:
        started = time.perf_counter()
        try:
            cs = ch.parse_chart(spec)
        except ch.ChartRejected as e:
            raise ToolError(f"rejected: {e}") from e
        if (source is None) == (artifact_id is None):
            raise ToolError("rejected: give exactly one of `source` or `artifact_id`")
        if artifact_id is not None:
            if pipeline:
                raise ToolError("rejected: `pipeline` applies to `source` only; artifacts are plotted as stored")
            # saving copies the source data into a new artifact: that needs export on the source servers too
            rows, meta = await _artifact_rows(artifact_id, permission="export" if save else "read")
            if cs.type == "histogram" and not meta.get("histogram"):
                raise ToolError("rejected: a histogram chart needs a histogram compute artifact, or a `source`")
            if meta.get("histogram"):
                cs = cs.model_copy(update={"type": "histogram", "column": cs.column or "value"})
        else:
            ops = _pipeline(pipeline)
            src_rows, meta = await fetch(source, settings.export_max_rows)
            try:
                df = tf.apply(tf.build_frame(src_rows), ops)
                if cs.type == "histogram":
                    result, _ = run_compute(df, parse_compute({"op": "histogram", "column": cs.column, "bins": cs.bins}),
                                            arrow_threshold=settings.compute_arrow_threshold_rows)
                    rows = ch.histogram_rows(result)  # counted by the Rust kernel, not by the chart
                else:
                    rows = redact_rows(tf.frame_to_rows(df.head(CHART_MAX_POINTS + 1)))
            except (tf.TransformRejected, ComputeRejected) as e:
                raise ToolError(f"rejected: {e}") from e
        if len(rows) > CHART_MAX_POINTS:
            raise ToolError(f"rejected: {len(rows)}+ points; aggregate first (compute/transform) to at most {CHART_MAX_POINTS}")
        try:
            vl, png = await asyncio.to_thread(ch.render, cs, rows)
        except ch.ChartRejected as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001
            log.exception("chart render failed")
            raise ToolError(f"chart render failed: {str(e)[:200]}") from e
        out: dict[str, Any] = {**meta, "type": cs.type, "points": len(rows), "png_bytes": len(png)}
        if save:
            out["artifact"] = await _save("chart", title or cs.title, {"spec": cs.model_dump(), "vegalite": vl, "rows": rows,
                                                                        "png_base64": base64.b64encode(png).decode()},
                                          derived_from=tuple(meta.get("sources") or ()))
        out["duration_ms"] = int((time.perf_counter() - started) * 1000)
        summary = orjson.dumps(out, default=str).decode()
        return ToolResult(content=[TextContent(type="text", text=summary),
                                   ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png")],
                          structured_content=out)

    async def report(outline: dict[str, Any], format: rp.ReportFormat = "docx", delivery: Delivery = "auto",
                     filename: str | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        ctx = current_tenant()
        try:
            o = rp.parse_outline(outline)
        except rp.ReportRejected as e:
            raise ToolError(f"rejected: {e}") from e
        ids = rp.artifact_ids(o)
        loaded = {}
        if len(ids) > rp.MAX_ARTIFACTS:
            raise ToolError(f"rejected: at most {rp.MAX_ARTIFACTS} distinct artifacts per report")
        total = 0
        for aid in ids:  # each re-proves ownership + export on every server the data came from (a report is a file)
            try:
                loaded[aid] = await artifacts.get(ctx, aid, permission="export")
            except ArtifactNotFound as e:
                raise ToolError(f"artifact not found: {aid}") from e
            total += loaded[aid][0].bytes
            if total > rp.MAX_TOTAL_BYTES:
                raise ToolError(f"rejected: referenced artifacts exceed {rp.MAX_TOTAL_BYTES // 1_000_000} MB")
        try:
            raw_brand = await resolver.r.get(f"branding:{ctx.company_id}")  # the caller's own company only
            resolved = rp.resolve(o, loaded, rp.brand_from(orjson.loads(raw_brand) if raw_brand else None))
            data = await asyncio.to_thread(rp.RENDERERS[format], resolved)
        except rp.ReportRejected as e:
            raise ToolError(f"rejected: {e}") from e
        except Exception as e:  # noqa: BLE001
            log.exception("report render failed")
            raise ToolError(f"report render failed: {str(e)[:200]}") from e
        name = safe_filename(filename or o.title, format, "report")
        try:
            delivered = await exporter.deliver(data, ctx=ctx, fmt=format, delivery=delivery, filename=name,
                                               dataset=layout.REPORTS, content_type=rp.CONTENT_TYPES[format])
        except Exception as e:  # noqa: BLE001
            log.exception("report delivery failed")
            raise ToolError("report delivery failed: object storage unavailable") from e
        return {"format": format, "title": o.title, "sections": len(o.sections), "artifacts": ids,
                "sources": resolved.sources, **delivered.as_dict(), "duration_ms": int((time.perf_counter() - started) * 1000)}

    async def monitor_create(name: str, source: dict[str, Any], op: dict[str, Any], condition: dict[str, Any],
                             interval_s: int = 300, pipeline: list[dict[str, Any]] | None = None,
                             channels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        try:
            spec = monitors.parse({"name": name, "source": source, "op": op, "condition": condition, "interval_s": interval_s,
                                   "pipeline": pipeline, "channels": channels or []})
            d, ev = await monitors.create(current_tenant(), spec)
        except mon.MonitorRejected as e:
            raise ToolError(f"rejected: {e}") from e
        return {**mon.public(d), "first_run": ev}

    async def monitor_list() -> dict[str, Any]:
        return {"monitors": await monitors.list_for(current_tenant())}

    async def monitor_status(monitor_id: str, limit: int = 10) -> dict[str, Any]:
        try:
            return await monitors.status(current_tenant(), monitor_id, limit)
        except mon.MonitorNotFound as e:
            raise ToolError("monitor not found") from e

    async def monitor_pause(monitor_id: str, paused: bool = True) -> dict[str, Any]:
        try:
            return await monitors.set_paused(current_tenant(), monitor_id, paused)
        except mon.MonitorNotFound as e:
            raise ToolError("monitor not found") from e

    async def monitor_delete(monitor_id: str) -> dict[str, Any]:
        try:
            await monitors.delete(current_tenant(), monitor_id)
        except mon.MonitorNotFound as e:
            raise ToolError("monitor not found") from e
        return {"deleted": monitor_id}

    async def list_artifacts(limit: int = 50) -> dict[str, Any]:
        try:
            items = await artifacts.list(current_tenant(), limit=limit)
        except Exception as e:  # noqa: BLE001
            log.exception("artifact list failed")
            raise ToolError("artifact list failed: object storage unavailable") from e
        return {"artifacts": items}

    async def get_artifact(artifact_id: str) -> dict[str, Any]:
        try:
            meta, payload = await artifacts.get(current_tenant(), artifact_id)
        except ArtifactNotFound as e:
            raise ToolError("artifact not found") from e
        except Exception as e:  # noqa: BLE001
            log.exception("artifact read failed")
            raise ToolError("artifact read failed: object storage unavailable") from e
        return {**meta.public(), "payload": payload}

    mcp.tool(compute, name="compute", description=(
        "ALWAYS use this for numeric or statistical work on data instead of calculating in your own reasoning: "
        "it runs compiled Rust kernels over the full result set and returns exact figures. ops: "
        "describe (count/mean/std/min/max/percentiles of a column), percentiles, histogram, group_stats (per-key "
        "count/sum/mean/min/max), top_k (value counts), distinct_count, correlation (pearson+spearman of two columns), "
        "outliers (iqr|zscore), time_bucket (time series aggregation by bucket), moving_average, linear_trend (OLS "
        "slope/intercept/r2 + forecast of `horizon` steps), growth (period-over-period abs/pct change with lag), pareto "
        "(sum per key with cumulative share and keys_to_threshold, e.g. 80/20), ab_test (Welch t-test of two groups: "
        "means, t, df, p_value, cohens_d), seasonality (count/mean/index per month|weekday|quarter|hour). Optional `pipeline` "
        "(same grammar as transform) runs first. transfer: auto (default; Arrow zero-copy hand-off for large inputs, "
        "list copy for small ones) | copy | arrow. save=true stores the result as an artifact (needs the export "
        f"permission) so charts/reports can reuse the exact figures by artifact_id. source: {source_doc}."))
    mcp.tool(transform, name="transform", description=(
        "Clean/reshape query results with a whitelisted pipeline (select, drop, rename, cast, filter, dedupe, fill_null, "
        "drop_nulls, trim, lower, upper, replace, to_datetime, sort, limit, aggregate, concat, arith, round, mask, hash). "
        f"source: {source_doc}. Returns JSON rows. save=true stores the rows as an artifact (needs the export permission)."))
    mcp.tool(export, name="export", description=(
        "Export query results (optionally cleaned with the same pipeline as `transform`) as csv or xlsx. "
        "Small files are returned inline (base64); large ones are uploaded and a short-lived download link is returned. "
        f"delivery: auto|inline|link. source: {source_doc}."))
    mcp.tool(chart, name="chart", output_schema=None, description=(
        "Draw a chart and return it as a PNG image. Data comes from `source` (optionally cleaned/aggregated with "
        "`pipeline`) or from a saved `artifact_id` (e.g. a compute result from any of your servers). Rows are plotted as "
        "given: aggregate first with compute/transform; histograms are counted by the Rust kernel. spec: {type: "
        "bar|line|area|scatter|histogram|heatmap, x: {field, type?}, y: {field, type?}, color?: {field, type?}, "
        "column (histogram), bins, title, sort: none|x|-x|y|-y, width, height}. Field types: quantitative|temporal|"
        f"nominal|ordinal (inferred if omitted). At most {CHART_MAX_POINTS} points. save=true stores the chart as an "
        f"artifact for reports (needs the export permission). source: {source_doc}."))
    mcp.tool(report, name="report", description=(
        "Write a progress report as docx (Word), pptx (PowerPoint) or pdf from an outline. Figures must come from saved "
        "artifacts: first run compute/transform/chart with save=true (on any of your servers), then reference their "
        "artifact_id here — never type numbers into kpi/table blocks yourself. outline: {title, subtitle?, author?, "
        "sections: [{heading, blocks: [{type: text, text} | {type: kpi, artifact_id, fields?, labels?, caption?} | "
        "{type: table, artifact_id, columns?, max_rows?, caption?} | {type: chart, artifact_id, caption?}]}]}. "
        "text supports paragraphs (blank line) and '- ' bullets. An appendix lists every artifact's server and query. "
        "Small files come back inline (base64), larger ones as a short-lived download link. Needs the export permission."))
    mcp.tool(monitor_create, name="monitor_create", description=(
        "Watch a metric: the server re-runs `source` (+ optional `pipeline`) through a compute `op` every `interval_s` "
        "seconds and checks `condition` {path, op: gt|gte|lt|lte|eq|ne, threshold} against the result (path is a key "
        "into the compute result, e.g. 'mean' or '0.count'). Runs once immediately and returns that result. It runs "
        "with this key's permissions and stops running if the key, user or access is suspended. Read results with "
        "monitor_status. channels: [] (no outbound channels are configured yet)."))
    mcp.tool(monitor_list, name="monitor_list", description="List your monitors on this server.")
    mcp.tool(monitor_status, name="monitor_status", description="Recent runs of one monitor (newest first): ok | breach | skipped (with reason) | error, with the measured value.")
    mcp.tool(monitor_pause, name="monitor_pause", description="Pause (paused=true) or resume (paused=false) a monitor.")
    mcp.tool(monitor_delete, name="monitor_delete", description="Delete a monitor and its history.")
    mcp.tool(list_artifacts, name="list_artifacts", description=(
        "List your saved artifacts (results stored with save=true by compute/transform/chart on any of your servers), "
        "newest first. Reuse them by artifact_id instead of recomputing or retyping numbers."))
    mcp.tool(get_artifact, name="get_artifact", description="Read one of your saved artifacts by artifact_id (exact stored figures/rows).")
    return artifacts


def _pipeline(raw: Any) -> list[Any]:
    try:
        return tf.parse_pipeline(raw)
    except tf.TransformRejected as e:
        raise ToolError(f"rejected: {e}") from e
