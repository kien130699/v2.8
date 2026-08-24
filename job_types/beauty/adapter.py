from __future__ import annotations
from typing import Any

class Adapter:
    async def prepare(self, manager: Any, instance: dict[str, Any]) -> str:
        return await manager.engine.ensure_beauty_profile(
            instance["id"], instance["name"], instance["config"], instance.get("engine_ref")
        )

    async def start(self, manager: Any, instance: dict[str, Any]) -> dict[str, Any]:
        ref = await self.prepare(manager, instance)
        manager.set_engine_ref(instance["id"], ref)
        return await manager.engine.run_beauty(ref, instance["config"])

    async def wait(self, manager: Any, instance: dict[str, Any], started: dict[str, Any]) -> dict[str, Any]:
        return await manager.engine.wait_beauty(started["engine_job_ids"])
