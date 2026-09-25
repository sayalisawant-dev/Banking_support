import os
from typing import Optional
from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp.mcp_client import MCPClient


def get_streamable_http_mcp_client(user_token: Optional[str] = None) -> MCPClient:
    """
    Returns an MCP Client for AgentCore Gateway compatible with Strands.

    Args:
        user_token: User JWT token with gateway:invoke scope.

    Note:
        mcp SDK v2 renamed streamablehttp_client → streamable_http_client
        and moved headers off the function onto an httpx2.AsyncClient.
        This function handles both v2 (httpx2) and v1 (headers kwarg) via try/except.
    """
    gateway_url = os.getenv("GATEWAY_URL")

    # Handle local development mode
    if os.getenv("LOCAL_DEV") == "1":
        from contextlib import nullcontext
        from types import SimpleNamespace

        return nullcontext(SimpleNamespace(list_tools_sync=lambda: []))

    if not gateway_url:
        raise RuntimeError("Missing required environment variable: GATEWAY_URL")

    if not user_token:
        raise RuntimeError("User token is required for Gateway access")

    try:
        # mcp SDK v2: headers must be passed via httpx2.AsyncClient
        import httpx2
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {user_token}"},
            timeout=httpx2.Timeout(30, read=300),
        )
        return MCPClient(lambda: streamable_http_client(gateway_url, http_client=http_client))
    except ImportError:
        # Fallback for mcp SDK v1 (headers passed directly)
        return MCPClient(lambda: streamable_http_client(gateway_url, headers={"Authorization": f"Bearer {user_token}"}))
