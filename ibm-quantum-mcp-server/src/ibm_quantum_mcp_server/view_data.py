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

"""What the four views draw, as JSON: circuits by layer, layouts, results and devices.

The views draw circuits and device maps themselves from this data; nothing here
renders a picture.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from qiskit_mcp_server.circuit_serialization import dump_qpy_circuit

from . import backends, devices, hardware, routing, runs


if TYPE_CHECKING:
    from qiskit import QuantumCircuit


_DIRECTIVES = {"barrier"}


def _param_text(value: Any) -> str | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return round(number, 6) if math.isfinite(number) else str(value)


def circuit_json(qc: QuantumCircuit, blocks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A circuit as qubits, clbits and layers of gates, each gate as early as it can go.

    Each gate keeps ``index``, its position in the circuit's instruction list, so a
    selection, a block and a focus name the same gate everywhere.
    """
    qubit_level = [0] * qc.num_qubits
    clbit_level = [0] * qc.num_clbits
    # Measurements take turns on the classical bus, so two never share a column and their
    # arrows and bit labels stay apart (a drawing column, not Qiskit's depth).
    bus_level = 0
    layers: list[list[dict[str, Any]]] = []
    for index, instruction in enumerate(qc.data):
        qubits = [qc.find_bit(q).index for q in instruction.qubits]
        clbits = [qc.find_bit(c).index for c in instruction.clbits]
        measure = instruction.operation.name == "measure"
        level = max(
            [qubit_level[q] for q in qubits]
            + [clbit_level[c] for c in clbits]
            + [bus_level if measure else 0]
        )
        while len(layers) <= level:
            layers.append([])
        layers[level].append(
            {
                "index": index,
                "name": instruction.operation.name,
                "qubits": qubits,
                "clbits": clbits,
                "params": [_param_text(p) for p in getattr(instruction.operation, "params", [])],
                "directive": instruction.operation.name in _DIRECTIVES,
            }
        )
        for q in qubits:
            qubit_level[q] = level + 1
        for c in clbits:
            clbit_level[c] = level + 1
        if measure:
            bus_level = level + 1
    layer_of = {g["index"]: n for n, layer in enumerate(layers) for g in layer}
    return {
        "num_qubits": qc.num_qubits,
        "num_clbits": qc.num_clbits,
        "qubits": [_bit_name(qc, q) for q in qc.qubits],
        "clbits": [_bit_name(qc, c) for c in qc.clbits],
        "layers": layers,
        "num_gates": len(qc.data),
        "used_qubits": sorted(
            {q for layer in layers for g in layer if not g["directive"] for q in g["qubits"]}
        ),
        "parameters": [p.name for p in qc.parameters],
        "blocks": [_block(b, qc, layer_of) for b in (blocks or [])],
        "counts": backends.circuit_counts(qc),
    }


def _bit_name(qc: QuantumCircuit, bit: Any) -> str:
    location = qc.find_bit(bit)
    if location.registers:
        register, index = location.registers[0]
        return f"{register.name}[{index}]"
    return f"{location.index}"


def _block(block: dict[str, Any], qc: QuantumCircuit, layer_of: dict[int, int]) -> dict[str, Any]:
    """A named range of gates, ``{name, start, end}`` in instruction order (end excluded)."""
    start = int(block.get("start", 0))
    end = int(block.get("end", len(qc.data)))
    gates = list(range(max(start, 0), min(end, len(qc.data))))
    layers = [layer_of[i] for i in gates if i in layer_of]
    qubits = sorted({qc.find_bit(q).index for i in gates for q in qc.data[i].qubits})
    return {
        "name": str(block.get("name") or "block"),
        "start": start,
        "end": end,
        "first_layer": min(layers) if layers else None,
        "last_layer": max(layers) if layers else None,
        "qubits": qubits,
    }


def _circuit_from(
    circuit: str | None, job_id: str | None, circuit_format: str = "auto"
) -> tuple[QuantumCircuit, dict[str, Any] | None]:
    if circuit:
        return runs.parse_circuit(circuit, circuit_format), None
    if job_id:
        record = runs.job_record(job_id)
        qasm = record.get("circuit_qasm3")
        if not qasm:
            raise runs.RunError(
                "job_has_no_circuit",
                f"Job {job_id} was not submitted through this server, so its circuit is not "
                "known. Give the circuit, or record the job with record_job.",
            )
        return runs.parse_circuit(qasm, "qasm3"), record
    raise runs.RunError("nothing_to_show", "Give a circuit or a job_id.")


def circuit_view(
    circuit: str | None, job_id: str | None, blocks: list[dict[str, Any]] | None
) -> dict[str, Any]:
    qc, record = _circuit_from(circuit, job_id)
    data = circuit_json(qc, blocks)
    data["qasm3"] = backends.dumps_qasm3(qc)
    data["qpy"] = dump_qpy_circuit(qc)
    data["job_id"] = job_id
    if record:
        data["backend"] = record.get("backend")
    return data


def device_json(name: str, device: Any) -> dict[str, Any]:
    return {
        "name": name,
        "num_qubits": device.num_qubits,
        "edges": [list(e) for e in devices.edges_of(device)],
        "positions": devices.positions(name, device),
        "two_qubit_gate": devices.two_qubit_gate(device),
    }


def _device(name: str) -> Any:
    kind, name = backends.classify(name)
    if kind == "fake":
        return backends.fake_backend(name)
    if kind == "hardware":
        return hardware.device(hardware.service("A view of a real device"), name)
    raise runs.RunError(
        "not_a_device",
        f"{name} is an ideal simulator with no qubit map. Pick a fake_<device> or a real one.",
    )


def layout_view(
    circuit: str | None,
    job_id: str | None,
    backend_name: str | None,
    optimization_level: int,
    initial_layout: list[int] | None,
    seed_transpiler: int | None,
) -> dict[str, Any]:
    if seed_transpiler is None:
        seed_transpiler = routing.DEFAULT_SEED
    qc, record = _circuit_from(circuit, job_id)
    if qc.num_clbits and not any(i.operation.name == "measure" for i in qc.data):
        qc.remove_final_measurements()
    name = backend_name or (record or {}).get("backend") or backends.DEFAULT_FAKE_BACKEND
    if record and not backend_name and record.get("backend_kind") in ("exact", "aer"):
        name = backends.DEFAULT_FAKE_BACKEND
    if (
        record
        and not backend_name
        and record.get("options", {}).get("optimization_level") is not None
    ):
        optimization_level = int(record["options"]["optimization_level"])
    device = _device(name)
    report = _layout_report(qc, device, optimization_level, initial_layout, seed_transpiler)
    final = report.pop("_circuit")
    routed = report.pop("_routed")
    remap = report.pop("_remap")
    routed_data = (
        _remapped(circuit_json(routed), remap) if routed is not None else circuit_json(final)
    )
    # Swaps by the routed circuit's drawn layers, so the view's steps and its swap list agree.
    report["swaps"] = [
        {"layer": n, "qubits": gate["qubits"]}
        for n, layer in enumerate(routed_data["layers"])
        for gate in layer
        if gate["name"] == "swap"
    ]
    return {
        "backend": name,
        "job_id": job_id,
        "logical": circuit_json(qc),
        "logical_qasm3": backends.dumps_qasm3(qc),
        "routed": routed_data,
        "routed_is_final": routed is None,
        "device": device_json(name, device),
        **report,
        "initial_layout_requested": initial_layout,
        "final_qasm3": backends.dumps_qasm3(final),
    }


def _layout_report(
    qc: QuantumCircuit,
    device: Any,
    optimization_level: int,
    initial_layout: list[int] | None,
    seed_transpiler: int | None,
) -> dict[str, Any]:
    result = routing.transpile_with_routing(
        qc, device, optimization_level, seed_transpiler, initial_layout=initial_layout
    )
    final = result["circuit"]
    return {
        "optimization_level": optimization_level,
        "before": backends.circuit_counts(qc),
        "after": backends.circuit_counts(final),
        "layout": backends.layout_of(final),
        "swaps": result["swaps"],
        "_circuit": final,
        "_routed": result["routed"],
        "_remap": result["remap"],
    }


def _remapped(data: dict[str, Any], remap: dict[int, int]) -> dict[str, Any]:
    """The routed circuit on the qubits the transpile finally chose."""
    for layer in data["layers"]:
        for gate in layer:
            gate["qubits"] = [remap.get(q, q) for q in gate["qubits"]]
    data["used_qubits"] = sorted(remap.get(q, q) for q in data["used_qubits"])
    data["qubits"] = [str(q) for q in range(data["num_qubits"])]
    return data


def results_view(
    job_ids: list[str],
    labels: list[str] | None,
    expected: list[int] | None,
    threshold: float = 0.0,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for i, job_id in enumerate(job_ids):
        record = runs.job_record(job_id)
        records.append(
            {
                "job_id": job_id,
                "label": (labels[i] if labels and i < len(labels) else None)
                or record.get("label")
                or record.get("backend"),
                "backend": record.get("backend"),
                "backend_kind": record.get("backend_kind"),
                "job_status": record.get("status"),
                "primitive": record.get("primitive"),
                "circuit_qasm3": record.get("circuit_qasm3"),
                "parameter_values": (record.get("pubs") or {}).get("parameter_values"),
                "observables": (record.get("pubs") or {}).get("observables"),
                "shots": record.get("shots") or (record.get("result") or {}).get("shots"),
                "done": record.get("result") is not None,
                "_record": record,
            }
        )
    done = [r for r in records if r["done"]]
    comparison = None
    if done:
        from . import results

        comparison = results.compare(
            [{**dict(r["_record"]), "label": r["label"]} for r in done], expected, threshold, True
        )
    for r in records:
        r.pop("_record")
    return {"jobs": records, "comparison": comparison, "expected": expected}


def backend_view(backend_name: str) -> dict[str, Any]:
    kind, name = backends.classify(backend_name)
    device = _device(name)
    out: dict[str, Any] = {
        **device_json(name, device),
        "kind": kind,
        "qubit_metrics": devices.qubit_metrics(device),
        "edge_metrics": devices.edge_metrics(device),
        "queue": None,
        "time_left_seconds": None,
    }
    if kind == "hardware":
        try:
            out["queue"] = device.status().pending_jobs
        except Exception:
            out["queue"] = None
        try:
            out["time_left_seconds"] = hardware.budget(hardware.service())["remaining_seconds"]
        except hardware.HardwareRefusal:
            out["time_left_seconds"] = None
    else:
        out["note"] = (
            "A fake device: the calibration qiskit-ibm-runtime ships with it, no queue and no "
            "time limit."
        )
    return out


def dump(qc: QuantumCircuit) -> str:
    return dump_qpy_circuit(qc)
