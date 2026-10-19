"""Regression tests for template capture after browser idle reclamation."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aistudio_api.infrastructure.browser.cdp_client import CDPPage
from aistudio_api.infrastructure.gateway.capture import RequestCaptureService
from aistudio_api.infrastructure.gateway.session import BrowserSession
from aistudio_api.infrastructure.gateway.session_state import SessionContext
from aistudio_api.infrastructure.gateway.wire_codec import AistudioWireCodec


@pytest.mark.parametrize("reclaimed", [False, True])
async def test_capture_waits_for_ready_template_after_cold_start_or_idle(
    monkeypatch, reclaimed
):
    session = BrowserSession(port=9222)
    capture = RequestCaptureService(session)
    model = "models/gemini-4-argon"

    def make_context(url):
        page = MagicMock(spec=CDPPage)
        page.is_closed.return_value = False
        page.evaluate = AsyncMock(return_value="!fresh_snapshot")
        return SessionContext(
            page=page,
            snap_key="snap_fn",
            auth_user="3",
            auth_file=None,
            base_template={
                "url": url,
                "headers": {"X-Goog-AuthUser": "3"},
                "body": json.dumps([model, [], [], []]),
            },
        )

    if reclaimed:
        session.set_ready_context(make_context("https://old.example/GenerateContent"))
        assert await capture.capture(prompt="before idle", model=model) is not None
        await session.close()
        assert session.active_context is None

    new_ctx = make_context("https://fresh.example/GenerateContent")

    async def restore_session(*args, **kwargs):
        session.set_ready_context(new_ctx)
        return new_ctx

    transition = AsyncMock(side_effect=restore_session)
    monkeypatch.setattr(session, "transition_to_account", transition)

    result = await capture.capture(prompt="after idle", model=model)

    transition.assert_awaited_once()
    assert result is not None
    assert result.url == new_ctx.base_template["url"]
    assert result.headers["X-Goog-AuthUser"] == "3"
    decoded = AistudioWireCodec().decode(result.body)
    assert decoded.model == model
    assert decoded.contents[0].parts[0].text == "after idle"
    assert decoded.snapshot == "!fresh_snapshot"

    # A second request reuses the same ready context, not a bootstrap fallback.
    assert await capture.capture(prompt="next request", model=model) is not None
    transition.assert_awaited_once()
