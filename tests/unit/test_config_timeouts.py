"""Upstream timeout defaults must coexist with fork heartbeat configuration."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, [600, 600, 15]),
        ({"AISTUDIO_STREAM_HEARTBEAT_INTERVAL": "0"}, [600, 600, 0]),
        ({"AISTUDIO_STREAM_HEARTBEAT_INTERVAL": "-1"}, [600, 600, 0]),
        (
            {
                "AISTUDIO_TIMEOUT_REPLAY": "90",
                "AISTUDIO_TIMEOUT_STREAM": "180",
                "AISTUDIO_STREAM_HEARTBEAT_INTERVAL": "5",
            },
            [90, 180, 5],
        ),
    ],
)
def test_timeout_and_heartbeat_configuration(overrides, expected):
    # Settings evaluates environment variables at import time; use a fresh process
    # rather than reloading the shared module and leaking state into other tests.
    env = os.environ.copy()
    for name in (
        "AISTUDIO_TIMEOUT_REPLAY",
        "AISTUDIO_TIMEOUT_STREAM",
        "AISTUDIO_STREAM_HEARTBEAT_INTERVAL",
    ):
        env.pop(name, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    env.update(overrides)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from aistudio_api.config import settings; "
                "print(json.dumps([settings.timeout_replay, settings.timeout_stream, "
                "settings.stream_heartbeat_interval]))"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == expected
