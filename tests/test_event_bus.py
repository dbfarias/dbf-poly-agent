"""Tests for bot/agent/events.py — EventBus pub/sub."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import asyncio

import pytest

from bot.agent.events import EventBus


class TestEventBus:
    @pytest.mark.asyncio
    async def test_emit_calls_subscribed_handler(self):
        bus = EventBus()
        results = []

        async def handler(**kwargs):
            results.append(kwargs)

        bus.on("test", handler)
        await bus.emit("test", x=1, y=2)

        assert len(results) == 1
        assert results[0] == {"x": 1, "y": 2}

    @pytest.mark.asyncio
    async def test_emit_calls_multiple_handlers(self):
        bus = EventBus()
        calls = []

        async def h1(**kw):
            calls.append("h1")

        async def h2(**kw):
            calls.append("h2")

        bus.on("evt", h1)
        bus.on("evt", h2)
        await bus.emit("evt")

        assert calls == ["h1", "h2"]

    @pytest.mark.asyncio
    async def test_emit_unknown_event_is_noop(self):
        bus = EventBus()
        await bus.emit("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_handler_error_does_not_affect_others(self):
        bus = EventBus()
        results = []

        async def bad(**kw):
            raise ValueError("boom")

        async def good(**kw):
            results.append("ok")

        bus.on("evt", bad)
        bus.on("evt", good)
        await bus.emit("evt")

        assert results == ["ok"]  # good ran despite bad failing

    @pytest.mark.asyncio
    async def test_off_removes_handler(self):
        bus = EventBus()
        calls = []

        async def handler(**kw):
            calls.append(1)

        bus.on("evt", handler)
        bus.off("evt", handler)
        await bus.emit("evt")

        assert calls == []

    @pytest.mark.asyncio
    async def test_off_unknown_handler_is_noop(self):
        bus = EventBus()

        async def handler(**kw):
            pass

        bus.off("evt", handler)  # Not subscribed — should not raise

    @pytest.mark.asyncio
    async def test_sync_handler_works(self):
        bus = EventBus()
        results = []

        def sync_handler(**kw):
            results.append(kw)

        bus.on("evt", sync_handler)
        await bus.emit("evt", val=42)

        assert results == [{"val": 42}]

    @pytest.mark.asyncio
    async def test_emit_safe_during_handler_removal(self):
        """Removing a handler during emit() doesn't crash (list snapshot)."""
        bus = EventBus()
        removed = False

        async def self_removing_handler(**kw):
            nonlocal removed
            bus.off("test", self_removing_handler)
            removed = True

        other_called = False

        async def other_handler(**kw):
            nonlocal other_called
            other_called = True

        bus.on("test", self_removing_handler)
        bus.on("test", other_handler)
        await bus.emit("test")

        assert removed
        assert other_called

    def test_engine_does_not_import_api(self):
        """Verify engine.py no longer imports from api/ package."""
        import ast

        with open("bot/agent/engine.py") as f:
            tree = ast.parse(f.read())

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("api."), (
                        f"engine.py still imports from api: {node.module}"
                    )
