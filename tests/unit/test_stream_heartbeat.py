"""Initial empty-text SSE heartbeats must not affect upstream generation."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.api.schemas import GeminiGenerateContentRequest
from aistudio_api.application import api_service_gemini as service
from aistudio_api.config import settings
from aistudio_api.domain.errors import RequestError


def heartbeat_payload(chunk):
    return json.loads(chunk.removeprefix("data: "))


@pytest.mark.asyncio
async def test_heartbeats_repeat_without_cancelling_upstream_and_stop_after_content():
    ready = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            await ready.wait()
            yield "first"
            await asyncio.sleep(0.03)
            yield "second"
        finally:
            closed.set()

    stream = service._with_initial_heartbeats(
        source(), model_path="models/gemini-test", interval=0.01
    )
    async with asyncio.timeout(1):
        initial = await anext(stream)
        assert heartbeat_payload(initial) == {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": ""}]}, "index": 0}
            ],
            "modelVersion": "models/gemini-test",
        }
        assert await anext(stream) == initial
        assert await anext(stream) == initial
        assert not closed.is_set()
        ready.set()
        assert [chunk async for chunk in stream] == ["first", "second"]
    assert closed.is_set()


@pytest.mark.asyncio
async def test_closing_stream_cancels_pending_generation():
    started = asyncio.Event()
    closed = asyncio.Event()

    async def source():
        try:
            started.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    stream = service._with_initial_heartbeats(
        source(), model_path="gemini-test", interval=15
    )
    await anext(stream)
    await started.wait()
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_disabled_heartbeats_preserve_stream():
    async def source():
        yield "first"
        yield "second"

    stream = service._with_initial_heartbeats(
        source(), model_path="gemini-test", interval=0
    )
    assert [chunk async for chunk in stream] == ["first", "second"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_path", ["gemini-4-argon", "models/gemini-4-argon"])
async def test_heartbeat_covers_account_preparation_and_preserves_retry(
    monkeypatch, model_path
):
    ready = asyncio.Event()

    async def prepare(*args, **kwargs):
        await ready.wait()

    monkeypatch.setattr(service, "ensure_active_account", prepare)
    monkeypatch.setattr(service, "record_rotator_event", MagicMock())
    monkeypatch.setattr(settings, "stream_heartbeat_interval", 15)
    attempts = 0

    async def generate(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RequestError(204, "No content")
        yield "body", "hello"

    client = MagicMock()
    client.stream_generate_content = generate
    request = GeminiGenerateContentRequest(
        contents=[{"role": "user", "parts": [{"text": "hi"}]}]
    )
    response = await service.handle_gemini_generate_content(
        model_path, request, client, stream=True
    )
    stream = response.body_iterator
    initial = heartbeat_payload(await anext(stream))
    assert initial["candidates"][0]["content"]["parts"] == [{"text": ""}]
    assert attempts == 0
    ready.set()
    chunks = [heartbeat_payload(chunk) async for chunk in stream]
    assert attempts == 2
    assert chunks[0]["candidates"][0]["content"]["parts"] == [{"text": "hello"}]
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"
    # gproxy rejects a change in modelVersion even when the only difference is
    # the models/ prefix. Include the heartbeat and terminal usage in this check.
    assert {chunk["modelVersion"] for chunk in [initial, *chunks]} == {
        "models/gemini-4-argon"
    }


@pytest.mark.asyncio
async def test_upstream_error_still_emits_gemini_sse_error(monkeypatch):
    monkeypatch.setattr(
        service, "ensure_active_account", AsyncMock(side_effect=ValueError("bad"))
    )
    monkeypatch.setattr(settings, "stream_heartbeat_interval", 15)
    request = GeminiGenerateContentRequest(
        contents=[{"role": "user", "parts": [{"text": "hi"}]}]
    )
    response = await service.handle_gemini_generate_content(
        "gemini-test", request, MagicMock(), stream=True
    )
    chunks = [heartbeat_payload(chunk) async for chunk in response.body_iterator]
    assert len(chunks) == 2
    assert chunks[-1]["error"]["code"] == 400
