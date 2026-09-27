"""external installation -- blocked placeholder (brief D14, O-5).

The external installation is an independent external product (ADR-006); Jarvis will
only ever be an MCP client of an installation's MCP-compatible tools on a
user's behalf. The panel does not expose MCP yet and its auth model and
approval treatment are open (`docs/mcp/external_installation.md`), so this manifest ships
`blocked`: no binding can be created for it and no tool is registered. A
later brief flips it -- with real tools, pins and a sidecar -- once the
panel exposes MCP.
"""

from connectors.manifest import (
    KIND_MCP,
    STATUS_BLOCKED,
    WORKSPACE_COMPANY,
    ConnectorManifest,
)
from core.channels import CHANNEL_CLI, CHANNEL_WEB

MANIFEST = ConnectorManifest(
    name="external_installation",
    version=1,
    display_name="external installation",
    kind=KIND_MCP,
    status=STATUS_BLOCKED,
    data_class="firm",
    workspace_types=frozenset({WORKSPACE_COMPANY}),
    read_channels=frozenset({CHANNEL_WEB, CHANNEL_CLI}),
    url_env="EXTERNAL_INSTALLATION_MCP_URL",
    tools=(),
)
