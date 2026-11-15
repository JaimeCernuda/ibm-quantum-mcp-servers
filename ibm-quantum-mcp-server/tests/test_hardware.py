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

"""The key and the hardware path (QC1, QC3, QS8), against qiskit-ibm-runtime's own
local service: its fake devices stand in for real ones and its primitives run there."""

from __future__ import annotations

import json
from typing import Any

import pytest
from ibm_quantum_mcp_server import hardware, runs
from qiskit_ibm_runtime.fake_provider.local_service import QiskitRuntimeLocalService

from .conftest import BELL, call


class RuntimeDouble(QiskitRuntimeLocalService):
    """The runtime's local service, with an instance's time and job lookup added."""

    def __init__(self, remaining: float | None = 600.0, limit: float | None = 600.0) -> None:
        super().__init__()
        self.remaining = remaining
        self.limit = limit
        self.submitted: dict[str, Any] = {}

    def backend(self, name: str = None, instance: str = None) -> Any:  # noqa: RUF013
        fake = super().backend("fake_" + name.removeprefix("ibm_"))
        double = self

        class Device(type(fake)):  # a device whose jobs this service can look up
            pass

        fake.__class__ = Device
        original = fake.run if hasattr(fake, "run") else None
        del original
        fake._double = double
        return fake

    def usage(self) -> dict[str, Any]:
        out: dict[str, Any] = {"usage_consumed_seconds": 0, "usage_period": "28 days"}
        if self.limit is not None:
            out["usage_limit_seconds"] = self.limit
            out["usage_remaining_seconds"] = self.remaining
        return out

    def job(self, job_id: str) -> Any:
        return self.submitted[job_id]

    def jobs(self, limit: int = 10) -> list[Any]:
        return list(self.submitted.values())[:limit]


@pytest.fixture
def double(monkeypatch):
    """A key in the environment; the service it opens is the runtime's local service."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    service = RuntimeDouble()
    calls: list[dict[str, Any]] = []

    def construct(**kwargs: Any) -> RuntimeDouble:
        calls.append(kwargs)
        return service

    monkeypatch.setenv("QISKIT_IBM_TOKEN", "a-test-key")
    monkeypatch.setenv("QISKIT_IBM_RUNTIME_MCP_INSTANCE", "crn:v1:test")
    monkeypatch.setattr(ibm_runtime, "QiskitRuntimeService", construct)
    from qiskit_ibm_runtime import EstimatorV2, SamplerV2

    for primitive in (SamplerV2, EstimatorV2):
        run = primitive.run

        def tracked(self, *a, _run=run, **k):  # keep every job so the service can find it
            job = _run(self, *a, **k)
            service.submitted[job.job_id()] = job
            return job

        monkeypatch.setattr(primitive, "run", tracked)
    service.calls = calls
    return service


async def test_without_a_key_a_hardware_run_says_what_it_needs(client, monkeypatch):
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    def never(**kwargs: Any) -> Any:
        raise AssertionError("the service must not be opened without a key")

    monkeypatch.setattr(ibm_runtime, "QiskitRuntimeService", never)
    out = await call(client, "run_sampler", circuit=BELL, backend_name="ibm_fez")
    assert out["reason"] == "key_needed"
    assert out["needs"] == ["token", "instance"]
    assert "local_exact" in out["message"]
    out = await call(client, "usage_info")
    assert out["reason"] == "key_needed"
    out = await call(client, "least_busy_backend")
    assert out["reason"] == "key_needed"


async def test_a_saved_account_on_disk_is_never_read(client, tmp_path, monkeypatch):
    """QQ5: a home folder with ~/.qiskit/qiskit-ibm.json and no key in CLIO."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    saved = tmp_path / "home" / ".qiskit"
    saved.mkdir(parents=True)
    (saved / "qiskit-ibm.json").write_text(
        json.dumps(
            {
                "default-ibm-quantum-platform": {
                    "channel": "ibm_quantum_platform",
                    "token": "saved-key",
                }
            }
        )
    )
    opened: list[Any] = []
    monkeypatch.setattr(ibm_runtime, "QiskitRuntimeService", lambda **k: opened.append(k))
    out = await call(client, "usage_info")
    assert out["reason"] == "key_needed"
    assert opened == []


def test_a_key_opens_the_service_without_saving_it(double, monkeypatch):
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    saved: list[Any] = []
    monkeypatch.setattr(
        ibm_runtime.QiskitRuntimeService,
        "save_account",
        lambda **k: saved.append(k),
        raising=False,
    )
    hardware.service()
    assert double.calls == [
        {"channel": "ibm_quantum_platform", "token": "a-test-key", "instance": "crn:v1:test"}
    ]
    assert saved == []


async def test_saved_account_tools_are_not_served_while_saved_accounts_are_off(client):
    names = {t.name for t in await client.list_tools()}
    assert not names & {"setup_ibm_quantum_account", "list_saved_accounts", "delete_saved_account"}


async def test_a_hardware_run_reads_the_time_left_first_and_reports_it(client, double):
    out = await call(client, "run_sampler", circuit=BELL, backend_name="ibm_fez", shots=1000)
    assert out["status"] == "success", out
    assert out["kind"] == "hardware"
    assert out["budget"]["remaining_seconds_before"] == 600.0
    assert 0 < out["budget"]["estimate_seconds"] < 10
    assert "estimated" in out["message"].lower()
    double.submitted[out["job_id"]].result()  # the device finishes the job
    done = await call(client, "get_job_results", job_id=out["job_id"])
    assert done["job_status"] == "DONE"
    assert sum(done["result"]["sets"][0]["counts"].values()) == 1000
    assert "seconds_used" in done["budget"]
    assert done["budget"]["remaining_seconds"] == 600.0


async def test_a_run_larger_than_the_time_left_is_refused_with_both_numbers(client, double):
    double.remaining = 3.0
    out = await call(client, "run_sampler", circuit=BELL, backend_name="ibm_fez", shots=100000)
    assert out.get("reason") == "budget_exceeded", out
    assert out["remaining_seconds"] == 3.0
    assert out["estimate_seconds"] > 3.0
    assert double.submitted == {}
    assert runs.store().list() == []


async def test_an_instance_without_a_limit_is_not_used(client, double):
    double.limit = None
    out = await call(client, "run_sampler", circuit=BELL, backend_name="ibm_fez")
    assert out["reason"] == "budget_unmetered"
    assert double.submitted == {}


async def test_a_hardware_estimator_batch_is_one_job(client, double):
    from .test_simulation import classifier_qasm, sets

    out = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables="ZZ",
        parameter_values=sets(5),
        backend_name="ibm_fez",
        precision=0.05,
    )
    assert out["status"] == "success", out
    assert len(double.submitted) == 1
    double.submitted[out["job_id"]].result()
    done = await call(client, "get_job_results", job_id=out["job_id"])
    assert done["result"]["num_sets"] == 5
    assert done["result"]["observables"] == ["ZZ"]


def test_the_estimate_counts_shots_circuits_and_the_per_shot_time():
    from ibm_quantum_mcp_server import backends, pubs
    from qiskit import QuantumCircuit

    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    device = backends.fake_backend("fake_fez")
    prepared = backends.prepare(qc, "fake_fez")
    one = hardware.estimate_seconds(
        "sampler", prepared, device, pubs.parameter_sets(qc, None), 1000
    )
    assert one["shots"] == 1000
    assert one["seconds"] == pytest.approx(1000 * one["per_shot_seconds"] + 2.0, abs=1e-3)
    assert 250e-6 <= one["per_shot_seconds"] < 1e-3
