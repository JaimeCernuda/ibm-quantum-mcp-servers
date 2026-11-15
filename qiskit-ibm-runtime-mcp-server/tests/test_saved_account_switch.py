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

"""QISKIT_MCP_NO_SAVED_ACCOUNT: the key comes only from the environment, never from disk."""

from unittest.mock import patch

import pytest

from qiskit_ibm_runtime_mcp_server import ibm_runtime


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setenv(ibm_runtime.NO_SAVED_ACCOUNT_ENV, "1")
    monkeypatch.delenv("QISKIT_IBM_TOKEN", raising=False)
    monkeypatch.delenv("QISKIT_IBM_RUNTIME_MCP_INSTANCE", raising=False)


def test_no_key_raises_without_opening_the_service(switch_on):
    with patch.object(ibm_runtime, "QiskitRuntimeService") as service:
        with pytest.raises(ibm_runtime.NoAccountKeyError):
            ibm_runtime.initialize_service()
        service.assert_not_called()


def test_the_environment_key_is_used_and_never_saved(switch_on, monkeypatch):
    monkeypatch.setenv("QISKIT_IBM_TOKEN", "env-key")
    monkeypatch.setenv("QISKIT_IBM_RUNTIME_MCP_INSTANCE", "crn:v1:x")
    with patch.object(ibm_runtime, "QiskitRuntimeService") as service:
        ibm_runtime.initialize_service()
        service.assert_called_once_with(
            channel="ibm_quantum_platform", token="env-key", instance="crn:v1:x"
        )
        service.save_account.assert_not_called()


def test_a_key_given_to_setup_is_not_saved(switch_on):
    with patch.object(ibm_runtime, "QiskitRuntimeService") as service:
        ibm_runtime.initialize_service(token="given-key")
        service.save_account.assert_not_called()
        assert service.call_args.kwargs["token"] == "given-key"


async def test_saved_account_tools_refuse(switch_on):
    listed = await ibm_runtime.list_saved_accounts()
    deleted = await ibm_runtime.delete_saved_account("default")
    assert listed["status"] == deleted["status"] == "error"
    assert ibm_runtime.NO_SAVED_ACCOUNT_ENV in listed["message"]
