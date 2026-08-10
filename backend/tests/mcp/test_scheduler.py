import asyncio

from app.mcp.scheduler import McpHealthScheduler


def test_scheduler_runs_due_clients_and_can_be_cancelled():
    seen = []

    async def check(client_key: str):
        seen.append(client_key)

    scheduler = McpHealthScheduler(["water", "gate"], check, interval_seconds=0)
    asyncio.run(scheduler.run_once())

    assert seen == ["water", "gate"]
    scheduler.cancel()
    assert scheduler.cancelled is True
