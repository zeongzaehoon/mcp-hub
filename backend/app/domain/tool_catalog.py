"""Static catalog of MCP tools per server type and the permission each tool requires.

MCP servers must register exactly these names (their test suites assert it). Backend rejects
api-keys whose allowed_tools are outside this catalog or whose permissions cannot cover them.
"""

from app.domain.enums import Permission, ServerType

TOOL_CATALOG: dict[ServerType, dict[str, Permission]] = {
    ServerType.POSTGRES: {
        "list_tables": Permission.READ,
        "describe_table": Permission.READ,
        "run_select": Permission.READ,
        "run_insert": Permission.CREATE,
        "run_update": Permission.UPDATE,
        "run_delete": Permission.DELETE,
        "transform": Permission.READ,
        "compute": Permission.READ,
        "export": Permission.EXPORT,
        "chart": Permission.READ,
        "report": Permission.EXPORT,
        "list_artifacts": Permission.READ,
        "get_artifact": Permission.READ,
        "monitor_create": Permission.READ,
        "monitor_list": Permission.READ,
        "monitor_status": Permission.READ,
        "monitor_pause": Permission.READ,
        "monitor_delete": Permission.READ,
    },
    ServerType.HACKER: {
        "list_tables": Permission.READ,
        "describe_table": Permission.READ,
        "run_select": Permission.READ,
        "transform": Permission.READ,
        "compute": Permission.READ,
        "export": Permission.EXPORT,
        "chart": Permission.READ,
        "report": Permission.EXPORT,
        "list_artifacts": Permission.READ,
        "get_artifact": Permission.READ,
        "monitor_create": Permission.READ,
        "monitor_list": Permission.READ,
        "monitor_status": Permission.READ,
        "monitor_pause": Permission.READ,
        "monitor_delete": Permission.READ,
    },
    ServerType.CLICKHOUSE: {
        "list_tables": Permission.READ,
        "describe_table": Permission.READ,
        "run_select": Permission.READ,
        "aggregate": Permission.READ,
        "transform": Permission.READ,
        "compute": Permission.READ,
        "export": Permission.EXPORT,
        "chart": Permission.READ,
        "report": Permission.EXPORT,
        "list_artifacts": Permission.READ,
        "get_artifact": Permission.READ,
        "monitor_create": Permission.READ,
        "monitor_list": Permission.READ,
        "monitor_status": Permission.READ,
        "monitor_pause": Permission.READ,
        "monitor_delete": Permission.READ,
    },
    ServerType.MONGO: {
        "list_collections": Permission.READ,
        "find": Permission.READ,
        "aggregate": Permission.READ,
        "insert_many": Permission.CREATE,
        "update_many": Permission.UPDATE,
        "delete_many": Permission.DELETE,
        "transform": Permission.READ,
        "compute": Permission.READ,
        "export": Permission.EXPORT,
        "chart": Permission.READ,
        "report": Permission.EXPORT,
        "list_artifacts": Permission.READ,
        "get_artifact": Permission.READ,
        "monitor_create": Permission.READ,
        "monitor_list": Permission.READ,
        "monitor_status": Permission.READ,
        "monitor_pause": Permission.READ,
        "monitor_delete": Permission.READ,
    },
}
# `transform`, `compute`, `export`, `chart`, `report`, `list_artifacts` and `get_artifact` are engine-agnostic (mcpcluster_common.datatools);
# every server exposes them, as are the monitor_* tools. Saving an artifact (save=true) additionally requires EXPORT — checked inside the tool.
# `compute` runs its numerics in Rust (mcpcluster_core) — LLMs are steered to it instead of calculating in-context.


def tools_for(server_type: ServerType) -> dict[str, Permission]:
    return TOOL_CATALOG[server_type]
