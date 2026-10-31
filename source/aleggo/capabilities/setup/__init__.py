"""Setup loop capability — A-leggo inspecting and changing its own configuration."""

from aleggo.capabilities.setup.capability import SetupCapability
from aleggo.capabilities.setup.tools import SETUP_TOOL_NAMES, SETUP_TOOL_TYPES

__all__ = ["SETUP_TOOL_NAMES", "SETUP_TOOL_TYPES", "SetupCapability"]
