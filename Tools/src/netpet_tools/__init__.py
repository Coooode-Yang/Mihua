"""netpet_tools: Independent network diagnostics and controlled action toolkit."""

from netpet_tools.base import ActionApprovalError, ErrorCode, ToolError, ToolExecutionError, ToolResult
from netpet_tools.config import (
    DnsConfig,
    MihomoControllerConfig,
    ProbeConfig,
    ProbeEndpointConfig,
    ProxyConfig,
    RoutingRuleConfig,
    ToolConfig,
)
from netpet_tools.service import ToolService

__version__ = "0.1.0"

__all__ = [
    "ActionApprovalError",
    "DnsConfig",
    "ErrorCode",
    "MihomoControllerConfig",
    "ProbeConfig",
    "ProbeEndpointConfig",
    "ProxyConfig",
    "RoutingRuleConfig",
    "ToolConfig",
    "ToolError",
    "ToolExecutionError",
    "ToolResult",
    "ToolService",
    "__version__",
]
