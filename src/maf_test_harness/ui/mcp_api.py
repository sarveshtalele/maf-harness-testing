"""MCP server settings API (docs/MCP.md). Secret values go in, never out."""

from __future__ import annotations

import asyncio

from .. import mcp_servers
from .presentation import public


def _view(server: dict) -> dict:
    status = mcp_servers.secret_status(server)
    return {**server, "secrets": status, "ready": all(status.values())}


class McpAPI:
    mcp_probe = None  # tests inject a fake: async (server) -> {"tools": [...], "count": n, "catalog": [{name, description}]}

    def _mcp_guard(self) -> None:
        if self.busy():
            raise RuntimeError("Wait for the current operation to finish before changing MCP servers.")

    def mcp_list(self) -> dict:
        presets = {
            key: {"label": p["label"], "note": p["note"], "server": mcp_servers.preset_server(key)}
            for key, p in mcp_servers.PRESETS.items()
        }
        return {
            "servers": [_view(s) for s in mcp_servers.load()],
            "presets": presets,
            "stages": list(mcp_servers.STAGES),
            "max_tools_per_server": mcp_servers.MAX_LISTED_TOOLS,
            "max_tools_per_stage": mcp_servers.MAX_MCP_TOOLS_PER_STAGE,
        }

    def mcp_save(self, body: dict) -> dict:
        with self.lock:
            self._mcp_guard()
            return _view(mcp_servers.upsert(body))

    def mcp_import(self, body: dict) -> dict:
        with self.lock:
            self._mcp_guard()
            parsed = mcp_servers.parse_import(body.get("text"))
            saved = mcp_servers.save_many([item["server"] for item in parsed])
            return {
                "imported": [
                    {
                        "source_key": item["source_key"],
                        "name": server["name"],
                        "secret_keys": item["secret_keys"],
                    }
                    for item, server in zip(parsed, saved, strict=True)
                ],
                "servers": [_view(s) for s in mcp_servers.load()],
            }

    def mcp_delete(self, name: str) -> dict:
        with self.lock:
            self._mcp_guard()
            mcp_servers.remove(name)
            return {"deleted": name}

    def mcp_set_secrets(self, name: str, body: dict) -> dict:
        with self.lock:
            self._mcp_guard()
            mcp_servers.set_secrets(name, body)
            return _view(mcp_servers.get(name))

    def mcp_test(self, name: str) -> dict:
        with self.lock:  # the guard is checked under the lock; the probe itself may take a long time
            self._mcp_guard()
            server = mcp_servers.get(name)
        try:
            result = asyncio.run((self.mcp_probe or mcp_servers.probe)(server))
        except Exception as exc:  # message is redacted: it may echo a URL or command output
            return {"ok": False, "message": public(f"{type(exc).__name__}: {str(exc)[:300]}")}
        catalog = result.get("catalog") or [{"name": t, "description": ""} for t in result["tools"]]
        with self.lock:
            if not self.busy():  # the catalogue is kept only while no run is using the configuration
                mcp_servers.set_discovered(name, catalog)
        return {"ok": True, "tools": result["tools"], "count": result["count"]}
