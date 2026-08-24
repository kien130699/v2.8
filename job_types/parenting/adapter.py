from __future__ import annotations
from typing import Any

class Adapter:
    async def prepare(self, manager: Any, instance: dict[str, Any]) -> str:
        return str(instance["config"].get("character_set_id") or "mother_girl_01")

    async def start(self, manager: Any, instance: dict[str, Any]) -> dict[str, Any]:
        return await manager.engine.run_parenting(instance["config"], instance["id"], instance["name"])

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        return await manager.engine.wait_parenting(started["engine_run_id"])
