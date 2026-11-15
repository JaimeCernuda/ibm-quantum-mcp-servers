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

"""The four views' tools and resources (QV1 to QV8), and the data they draw."""

from __future__ import annotations

import pytest
from ibm_quantum_mcp_server import backends, view_data

from .conftest import BELL, call


GHZ5 = """OPENQASM 3.0;
include "stdgates.inc";
qubit[5] q;
bit[5] c;
h q[0];
cx q[0], q[1];
cx q[0], q[2];
cx q[0], q[3];
cx q[0], q[4];
c = measure q;
"""


async def test_each_view_is_a_ui_resource_with_an_empty_csp(client):
    resources = {str(r.uri): r for r in await client.list_resources()}
    for view in ("circuit", "layout", "results", "backend"):
        uri = f"ui://ibm-quantum/{view}"
        assert resources[uri].mimeType == "text/html;profile=mcp-app"
        assert resources[uri].meta["ui"]["csp"] == {}
        html = (await client.read_resource(uri))[0].text
        assert f'window.__QUANTUM_VIEW__ = "{view}"' in html


async def test_opening_tools_name_their_view_and_the_data_tools_are_app_only(client):
    tools = {t.name: t for t in await client.list_tools()}
    for view in ("circuit", "layout", "results", "backend"):
        assert tools[f"view_{view}"].meta["ui"]["resourceUri"] == f"ui://ibm-quantum/{view}"
        assert tools[f"view_data_{view}"].meta["ui"]["visibility"] == ["app"]
    state = tools["view_circuit"].meta["ai.iowarp.clio/state"]
    assert {"key": "selected_gate", "tool": "focus"}.items() <= next(
        e for e in state if e["key"] == "selected_gate"
    ).items()


async def test_view_circuit_says_what_it_opened(client):
    result = await client.call_tool(
        "view_circuit", {"circuit": GHZ5, "blocks": [{"name": "entangle", "start": 1, "end": 5}]}
    )
    text = result.content[0].text
    assert "5 qubits" in text and "4 two-qubit gates" in text and "entangle" in text
    assert result.structured_content["view"] == "circuit"
    assert result.structured_content["request"]["circuit"] == GHZ5


async def test_a_view_of_an_unknown_job_is_a_typed_failure(client):
    result = await client.call_tool(
        "view_circuit", {"job_id": "local-nothere"}, raise_on_error=False
    )
    assert result.structured_content["reason"] == "job_not_found"


def test_circuit_layers_keep_gate_indices_and_put_measurements_one_per_column():
    from qiskit import qasm3

    data = view_data.circuit_json(qasm3.loads(GHZ5), [{"name": "entangle", "start": 1, "end": 5}])
    gates = [g for layer in data["layers"] for g in layer]
    assert sorted(g["index"] for g in gates) == list(range(10))
    measure_layers = [
        n for n, layer in enumerate(data["layers"]) for g in layer if g["name"] == "measure"
    ]
    assert len(measure_layers) == len(set(measure_layers)) == 5
    block = data["blocks"][0]
    assert block["qubits"] == [0, 1, 2, 3, 4]
    assert block["first_layer"] == 1


@pytest.mark.parametrize("level", [0, 1, 3])
def test_layout_swaps_sit_on_used_couplings_and_follow_the_routed_layers(level):
    data = view_data.layout_view(GHZ5, None, "fake_fez", level, None, None)
    edges = {tuple(e) for e in data["device"]["edges"]}
    used = set(data["layout"]["physical_qubits"])
    for swap in data["swaps"]:
        assert tuple(sorted(swap["qubits"])) in edges
        assert set(swap["qubits"]) <= used
        layer = data["routed"]["layers"][swap["layer"]]
        assert any(g["name"] == "swap" and g["qubits"] == swap["qubits"] for g in layer)
    assert set(data["routed"]["used_qubits"]) <= used
    assert len(data["device"]["positions"]) == 156


def test_a_layout_is_the_same_each_time():
    a = view_data.layout_view(GHZ5, None, "fake_fez", 1, None, None)
    b = view_data.layout_view(GHZ5, None, "fake_fez", 1, None, None)
    assert a["layout"] == b["layout"] and a["swaps"] == b["swaps"]


def test_an_initial_layout_places_the_circuit():
    data = view_data.layout_view(GHZ5, None, "fake_fez", 1, [0, 1, 2, 3, 15], None)
    assert data["initial_layout_requested"] == [0, 1, 2, 3, 15]


def test_the_backend_view_reads_the_calibration_the_tool_reports():
    from ibm_quantum_mcp_server import devices

    data = view_data.backend_view("fake_fez")
    calibration = devices.calibration(backends.fake_backend("fake_fez"), "fake_fez", [0, 1])
    two_qubit = {
        tuple(g["qubits"]): g["error"] for g in calibration["gate_errors"] if len(g["qubits"]) == 2
    }
    shown = {tuple(m["edge"]): m["two_qubit_error"] for m in data["edge_metrics"]}
    assert two_qubit[(0, 1)] == shown[(0, 1)]
    assert (
        data["qubit_metrics"][0]["readout_error"]
        == calibration["qubit_calibration"][0]["readout_error"]
    )


async def test_an_exact_sampler_job_carries_exact_probabilities(client):
    out = await call(client, "run_sampler", circuit=BELL, backend_name="local_exact")
    exact = out["result"]["sets"][0]["exact_probabilities"]
    assert exact == pytest.approx({"00": 0.5, "11": 0.5})


async def test_results_view_summarizes_a_comparison(client):
    exact = await call(client, "run_sampler", circuit=BELL, backend_name="local_exact")
    noisy = await call(client, "run_sampler", circuit=BELL, backend_name="fake_fez", seed=3)
    result = await client.call_tool(
        "view_results",
        {"job_ids": [exact["job_id"], noisy["job_id"]], "labels": ["exact", "noisy"]},
    )
    text = result.content[0].text
    assert "noisy: total variation distance" in text
    data = await call(client, "view_data_results", job_ids=[exact["job_id"], noisy["job_id"]])
    tvd = data["comparison"]["sets"][0]["total_variation_distance"][noisy["job_id"]]
    assert f"{tvd:.3f}" in text
