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

"""Local simulation under the one job model (QS3 to QS7)."""

from __future__ import annotations

import numpy as np
import pytest
from ibm_quantum_mcp_server import results, runs
from qiskit import qasm3
from qiskit.circuit.library import real_amplitudes, zz_feature_map

from .conftest import BELL, call


def classifier_qasm() -> str:
    """The classifier's circuit as an agent writes it: standard gates, named parameters."""
    from qiskit import transpile

    circuit = zz_feature_map(2).compose(real_amplitudes(2, reps=1))
    return qasm3.dumps(transpile(circuit, basis_gates=["h", "p", "cx", "ry"]))


def sets(n: int = 20) -> list[list[float]]:
    return np.random.default_rng(4).uniform(0, np.pi, (n, 6)).round(6).tolist()


async def test_bell_on_the_default_backend_is_ideal_and_local(client):
    out = await call(client, "run_sampler", circuit=BELL, seed=3)
    assert out["status"] == "success"
    assert out["job_id"].startswith("local-")
    assert out["backend"] == "local_aer"
    assert out["job_status"] == "DONE"
    counts = out["result"]["sets"][0]["counts"]
    assert set(counts) == {"00", "11"}
    assert sum(counts.values()) == 1000


async def test_a_fake_device_adds_noise_and_reports_its_transpile(client):
    out = await call(client, "run_sampler", circuit=BELL, backend_name="fake_fez", seed=3)
    assert out["status"] == "success", out
    counts = out["result"]["sets"][0]["counts"]
    assert {"01", "10"} & set(counts)
    assert out["transpiled"]["two_qubit_gates"] >= 1
    assert len(out["transpiled"]["layout"]["initial"]) == 2
    record = runs.store().get(out["job_id"])
    assert record["circuit"]["executed_qasm3"].startswith("OPENQASM 3.0")
    assert record["circuit"]["transpiled"] is True


async def test_a_batch_of_twenty_sets_is_one_estimator_job(client):
    out = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables="ZZ",
        parameter_values=sets(),
        backend_name="local_exact",
    )
    assert out["status"] == "success", out
    result = out["result"]
    assert result["num_sets"] == 20
    assert all(len(s["values"]) == 1 for s in result["sets"])
    assert all(s["standard_errors"] == [0.0] for s in result["sets"])
    assert len(runs.store().list()) == 1


async def test_several_observables_give_a_value_each(client):
    out = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables=["ZZ", "ZI", [["XX", 0.5], ["YY", 0.5]]],
        parameter_values=sets(3),
        backend_name="local_exact",
    )
    assert out["result"]["observables"] == ["ZZ", "ZI", "0.5·XX + 0.5·YY"]
    assert [len(s["values"]) for s in out["result"]["sets"]] == [3, 3, 3]


async def test_parameter_errors_are_typed(client):
    out = await call(client, "run_estimator", circuit=classifier_qasm(), observables="ZZ")
    assert out["reason"] == "parameters_invalid"
    assert "6 parameters" in out["message"]
    out = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables="ZZZ",
        parameter_values=sets(1)[0],
    )
    assert out["reason"] == "observables_invalid"


async def test_sampling_an_unmeasured_circuit_is_refused(client):
    out = await call(client, "run_sampler", circuit=BELL.replace("c = measure q;\n", ""))
    assert out["reason"] == "no_measurements"


async def test_an_unknown_fake_device_is_named(client):
    out = await call(client, "run_sampler", circuit=BELL, backend_name="fake_nowhere")
    assert out["reason"] == "backend_not_found"
    assert "fake_fez" in out["message"]


async def test_compare_jobs_aligns_counts_with_total_variation_distance(client):
    ideal = await call(client, "run_sampler", circuit=BELL, seed=1)
    noisy = await call(client, "run_sampler", circuit=BELL, backend_name="fake_fez", seed=1)
    out = await call(client, "compare_jobs", job_ids=[ideal["job_id"], noisy["job_id"]])
    assert out["status"] == "success"
    assert out["same_circuit"] is True
    tvd = out["sets"][0]["total_variation_distance"][noisy["job_id"]]
    expected = results.total_variation_distance(
        ideal["result"]["sets"][0]["counts"], noisy["result"]["sets"][0]["counts"]
    )
    assert tvd == pytest.approx(expected)
    assert 0 < tvd < 0.2


async def test_compare_jobs_scores_a_classifier_batch(client):
    exact = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables="ZZ",
        parameter_values=sets(),
        backend_name="local_exact",
    )
    labels = [int(s["values"][0] >= 0) for s in exact["result"]["sets"]]
    noisy = await call(
        client,
        "run_estimator",
        circuit=classifier_qasm(),
        observables="ZZ",
        parameter_values=sets(),
        backend_name="fake_fez",
        seed=2,
    )
    out = await call(
        client,
        "compare_jobs",
        job_ids=[exact["job_id"], noisy["job_id"]],
        expected=labels,
        labels=["exact", "noisy"],
    )
    cls = out["classification"]
    assert cls["accuracy"][exact["job_id"]] == 1.0
    flips = cls["flipped_from_reference"][noisy["job_id"]]
    assert cls["accuracy"][noisy["job_id"]] == pytest.approx(1 - len(flips) / 20)
    assert [j["label"] for j in out["jobs"]] == ["exact", "noisy"]


async def test_comparing_different_primitives_is_refused(client):
    a = await call(client, "run_sampler", circuit=BELL)
    b = await call(
        client, "run_estimator", circuit=BELL, observables="ZZ", backend_name="local_exact"
    )
    out = await call(client, "compare_jobs", job_ids=[a["job_id"], b["job_id"]])
    assert out["reason"] == "jobs_not_comparable"


async def test_a_recorded_job_compares_with_a_server_job(client):
    server = await call(client, "run_sampler", circuit=BELL, seed=5)
    recorded = await call(
        client,
        "record_job",
        circuit=BELL,
        primitive="sampler",
        backend_name="local_aer",
        counts={"00": 480, "11": 520},
        label="from run_code",
    )
    assert recorded["job_id"].startswith("local-")
    out = await call(client, "compare_jobs", job_ids=[server["job_id"], recorded["job_id"]])
    assert out["status"] == "success"
    assert out["jobs"][1]["label"] == "from run_code"


async def test_local_jobs_survive_a_server_restart(client):
    first = await call(client, "run_sampler", circuit=BELL, seed=9)
    runs.reset()  # what a new server process sees: the same state folder
    listed = await call(client, "list_my_jobs")
    assert [j["job_id"] for j in listed["local_jobs"]] == [first["job_id"]]
    again = await call(client, "get_job_results", job_id=first["job_id"])
    assert again["result"] == first["result"]
    assert "No API key" in listed["hardware_jobs_note"]


def test_a_job_left_running_by_an_earlier_server_reads_as_interrupted():
    store = runs.store()
    store.save({"job_id": "local-abc", "kind": "local", "status": "RUNNING"})
    runs.reset()
    record = runs.store().get("local-abc")
    assert record["status"] == "ERROR"
    assert record["error"]["reason"] == "interrupted"


async def test_a_queued_local_job_can_be_cancelled(client):
    import threading

    gate = threading.Event()
    record = {"job_id": "local-wait", "kind": "local", "primitive": "sampler"}
    runs.runner().submit(record, lambda: (gate.wait(5), {"primitive": "sampler"})[1])
    out = await call(client, "cancel_job", job_id="local-wait")
    gate.set()
    assert out["status"] == "success"
    assert runs.store().get("local-wait")["status"] == "CANCELLED"
