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

"""How a circuit lands on a device: its layout, the swaps routing inserted, and the
transpiled circuit, from one run of the preset pass manager."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qiskit.converters import circuit_to_dag, dag_to_circuit
from qiskit.transpiler import generate_preset_pass_manager

from .backends import circuit_counts, dumps_qasm3, layout_of


if TYPE_CHECKING:
    from qiskit import QuantumCircuit


_ROUTING_PASSES = {"SabreLayout", "SabreSwap", "BasicSwap", "LookaheadSwap", "StochasticSwap"}


def transpile_with_routing(
    circuit: QuantumCircuit,
    backend: Any,
    optimization_level: int = 1,
    seed_transpiler: int | None = None,
) -> dict[str, Any]:
    """Transpile as a hardware run does, keeping the routed circuit before its swaps are
    translated into native gates."""
    pm = generate_preset_pass_manager(
        backend=backend, optimization_level=optimization_level, seed_transpiler=seed_transpiler
    )
    captured: dict[str, Any] = {}
    moves: list[tuple[Any, list[Any]]] = []

    def callback(pass_: Any, dag: Any, property_set: Any = None, **_: Any) -> None:
        # VF2PostLayout may move the routed circuit to better qubits, at level 3 more
        # than once; keep each move with the qubits it was made on.
        if (
            type(pass_).__name__ == "VF2PostLayout"
            and property_set is not None
            and property_set["post_layout"] is not None
            and "routed" in captured
        ):
            moves.append((property_set["post_layout"], list(dag.qubits)))
        # Sabre lays out and routes in one pass at levels 1 to 3; the routing stage's
        # swap pass does it otherwise. The last of them leaves the routed circuit.
        if type(pass_).__name__ in _ROUTING_PASSES:
            captured["routed"] = dag_to_circuit(dag, copy_operations=True)

    final = pm.run(circuit, callback=callback)
    routed = captured.get("routed")
    swaps = swaps_in(routed) if routed is not None else []
    for post, qubits in moves:
        for swap in swaps:
            swap["qubits"] = [post[qubits[q]] for q in swap["qubits"]]
    return {"circuit": final, "routed": routed, "swaps": swaps}


def swaps_in(routed: QuantumCircuit) -> list[dict[str, Any]]:
    """Each swap routing inserted: its layer in the routed circuit and its physical qubits."""
    dag = circuit_to_dag(routed)
    out = []
    for layer_index, layer in enumerate(dag.layers()):
        for node in layer["graph"].op_nodes():
            if node.op.name == "swap":
                qubits = [routed.find_bit(q).index for q in node.qargs]
                out.append({"layer": layer_index, "qubits": qubits})
    return out


def layout_report(
    circuit: QuantumCircuit,
    backend: Any,
    optimization_level: int = 1,
    seed_transpiler: int | None = None,
) -> dict[str, Any]:
    """What a transpile to a device did: counts before and after, the layout and swaps."""
    routed = transpile_with_routing(circuit, backend, optimization_level, seed_transpiler)
    final = routed["circuit"]
    return {
        "optimization_level": optimization_level,
        "before": circuit_counts(circuit),
        "after": circuit_counts(final),
        "layout": layout_of(final),
        "swaps": routed["swaps"],
        "transpiled_qasm3": dumps_qasm3(final),
        "_circuit": final,
    }
