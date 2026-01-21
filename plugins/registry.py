from __future__ import annotations

from .archive_org import ArchiveOrgPlugin
from .magnet import MagnetPlugin


_PLUGINS = [
    MagnetPlugin(),
    ArchiveOrgPlugin(),
]


def list_plugins():
    return [plugin.name for plugin in _PLUGINS]


def get_plugin_for_uri(uri: str):
    for plugin in _PLUGINS:
        if plugin.can_handle(uri):
            return plugin
    return None
