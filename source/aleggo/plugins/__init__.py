"""A-leggo plugin package — entry-point discovery lives in ``loader``."""

from aleggo.plugins.loader import (
    PluginManifest,
    discover_plugins,
    load_plugin_capability,
)

__all__ = [
    "PluginManifest",
    "discover_plugins",
    "load_plugin_capability",
]
