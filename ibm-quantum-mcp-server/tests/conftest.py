# This code is part of Qiskit.
#
# (C) Copyright IBM 2025.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Fixtures: a fresh state folder per test, no saved account, no key."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import Client


BELL = """OPENQASM 3.0;
include "stdgates.inc";
bit[2] c;
qubit[2] q;
h q[0];
cx q[0], q[1];
c = measure q;
"""


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Each test has its own state folder and home, saved accounts off, and no key."""
    from ibm_quantum_mcp_server import runs

    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    monkeypatch.setenv("IBM_QUANTUM_MCP_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("QISKIT_MCP_NO_SAVED_ACCOUNT", "1")
    monkeypatch.delenv("QISKIT_IBM_TOKEN", raising=False)
    monkeypatch.delenv("QISKIT_IBM_RUNTIME_MCP_INSTANCE", raising=False)
    ibm_runtime.service = None
    runs.reset()
    yield
    ibm_runtime.service = None
    runs.reset()


@pytest.fixture
async def client():
    from ibm_quantum_mcp_server.server import mcp

    async with Client(mcp) as c:
        yield c


async def call(client: Client, name: str, **arguments: Any) -> dict[str, Any]:
    """Call a tool and return its structured result."""
    result = await client.call_tool(name, arguments, raise_on_error=False)
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if set(data) == {"result"} else data
    return json.loads(result.content[0].text)
