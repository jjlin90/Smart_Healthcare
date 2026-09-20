"""Prometheus metrics shared by the API, coordinator and MCP tool server."""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "medagent_http_requests_total",
    "HTTP requests received by the API",
    ("method", "path", "status"),
)
HTTP_LATENCY = Histogram(
    "medagent_http_request_duration_seconds",
    "HTTP response creation latency",
    ("method", "path"),
)
AGENT_CALLS = Counter(
    "medagent_agent_calls_total",
    "A2A agent calls",
    ("agent", "status"),
)
AGENT_LATENCY = Histogram(
    "medagent_agent_call_duration_seconds",
    "A2A agent call latency",
    ("agent",),
)
MCP_TOOL_CALLS = Counter(
    "medagent_mcp_tool_calls_total",
    "MCP tool calls initiated by ReAct agents",
    ("agent", "tool", "status"),
)
