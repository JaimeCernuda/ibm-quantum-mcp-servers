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

"""Where a circuit runs: exact, ideal shots, a device's noise, or the device.

``backend_name`` takes four kinds of value, and only the last needs an account:

=================  =========================================================
``local_exact``    Qiskit's statevector primitives: exact expectation values
``local_aer``      ``AerSimulator``: ideal shots (the default)
``fake_<name>``    ``AerSimulator.from_backend`` on that fake device: its noise,
                   coupling map and basis gates, transpiled as hardware would be
any other name     the real device, through qiskit-ibm-runtime
=================  =========================================================
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any, Literal

from qiskit import QuantumCircuit, qasm3
from qiskit.transpiler import generate_preset_pass_manager


BackendKind = Literal["exact", "aer", "fake", "hardware"]

LOCAL_EXACT = "local_exact"
LOCAL_AER = "local_aer"
FAKE_PREFIX = "fake_"
#: Used when a call names no backend. Hardware is never the default.
DEFAULT_BACKEND = LOCAL_AER
#: The Heron r2 fake device closest to the devices the Open Plan offers.
DEFAULT_FAKE_BACKEND = "fake_fez"

_NOT_GATES = {"barrier", "measure", "reset", "delay", "store"}


class BackendError(ValueError):
    """A backend name that does not name a backend this server can use."""


def classify(backend_name: str | None) -> tuple[BackendKind, str]:
    """Return the kind of backend a name means, and the name to record."""
    name = (backend_name or "").strip() or DEFAULT_BACKEND
    if name == LOCAL_EXACT:
        return "exact", name
    if name == LOCAL_AER:
        return "aer", name
    if name.startswith(FAKE_PREFIX):
        fake_backend(name)  # raises BackendError for an unknown fake device
        return "fake", name
    return "hardware", name


def is_local(backend_name: str | None) -> bool:
    """Whether a run on this backend stays on this machine."""
    return classify(backend_name)[0] != "hardware"


@functools.lru_cache(maxsize=1)
def _fake_provider() -> Any:
    from qiskit_ibm_runtime.fake_provider import FakeProviderForBackendV2

    return FakeProviderForBackendV2()


@functools.lru_cache(maxsize=1)
def fake_backend_names() -> tuple[str, ...]:
    """Every fake device qiskit-ibm-runtime ships, by name."""
    return tuple(sorted(backend.name for backend in _fake_provider().backends()))


@functools.lru_cache(maxsize=16)
def fake_backend(name: str) -> Any:
    """The fake device of this name (``fake_fez``)."""
    if name not in fake_backend_names():
        raise BackendError(
            f"No fake backend named {name!r}. Fake backends: {', '.join(fake_backend_names())}."
        )
    return _fake_provider().backend(name)


@functools.lru_cache(maxsize=4)
def noisy_simulator(name: str) -> Any:
    """An ``AerSimulator`` carrying the fake device's noise model, built once."""
    from qiskit_aer import AerSimulator

    return AerSimulator.from_backend(fake_backend(name))


@functools.lru_cache(maxsize=1)
def ideal_simulator() -> Any:
    from qiskit_aer import AerSimulator

    return AerSimulator()


def fake_backend_summary(name: str) -> dict[str, Any]:
    """What a fake device is, for lists and views."""
    backend = fake_backend(name)
    processor = getattr(backend, "processor_type", None) or {}
    two_qubit = sorted(
        op for op in backend.operation_names if op not in _NOT_GATES and _arity(backend, op) == 2
    )
    return {
        "name": name,
        "kind": "fake",
        "num_qubits": backend.num_qubits,
        "processor": " ".join(str(v) for v in processor.values()) if processor else None,
        "two_qubit_gates": two_qubit,
        "basis_gates": sorted(op for op in backend.operation_names if op not in _NOT_GATES),
    }


def _arity(backend: Any, op: str) -> int:
    try:
        return int(backend.target.operation_from_name(op).num_qubits)
    except Exception:  # an instruction the target cannot describe
        return 0


def two_qubit_gate_count(circuit: QuantumCircuit) -> int:
    """Gates acting on two or more qubits, not counting barriers and measurements."""
    return sum(
        1
        for instruction in circuit.data
        if instruction.operation.name not in _NOT_GATES and len(instruction.qubits) >= 2
    )


def circuit_counts(circuit: QuantumCircuit) -> dict[str, Any]:
    """Qubits, depth, gate counts and two-qubit gates of a circuit."""
    ops = circuit.count_ops()
    return {
        "num_qubits": circuit.num_qubits,
        "num_clbits": circuit.num_clbits,
        "depth": circuit.depth(),
        "size": circuit.size(),
        "two_qubit_gates": two_qubit_gate_count(circuit),
        "gate_counts": {str(k): int(v) for k, v in ops.items()},
        "num_parameters": circuit.num_parameters,
    }


def layout_of(circuit: QuantumCircuit) -> dict[str, Any] | None:
    """Where a transpiled circuit's qubits sit on the device.

    ``initial`` maps each of the logical circuit's qubits (by index) to the physical
    qubit it starts on; ``final`` is where each ends after the routing's swaps.
    """
    layout = circuit.layout
    if layout is None:
        return None
    initial = layout.initial_index_layout(filter_ancillas=True)
    final = layout.final_index_layout(filter_ancillas=True)
    return {
        "initial": list(initial),
        "final": list(final),
        "physical_qubits": sorted(set(initial) | set(final)),
    }


def used_qubits(circuit: QuantumCircuit) -> list[int]:
    """Indices of the qubits any instruction touches."""
    used: set[int] = set()
    for instruction in circuit.data:
        for qubit in instruction.qubits:
            used.add(circuit.find_bit(qubit).index)
    return sorted(used)


def dumps_qasm3(circuit: QuantumCircuit) -> str:
    """OpenQASM 3 for a circuit, logical or transpiled.

    A circuit whose custom gates the exporter cannot define (a gate read back from
    QASM with a mismatched definition) is written in standard gates instead.
    """
    try:
        return str(qasm3.dumps(circuit))
    except qasm3.QASM3ExporterError:
        pass
    from qiskit import transpile

    standard = transpile(
        circuit,
        basis_gates=["h", "x", "y", "z", "s", "sdg", "sx", "rx", "ry", "rz", "p", "u", "cx", "cz"],
        optimization_level=0,
    )
    return str(qasm3.dumps(standard))


@dataclass
class Prepared:
    """A circuit made ready for one backend."""

    kind: BackendKind
    backend_name: str
    logical: QuantumCircuit
    executable: QuantumCircuit
    optimization_level: int
    transpiled: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> dict[str, Any]:
        """What the run's record says about the circuit it ran."""
        out: dict[str, Any] = {
            "logical": circuit_counts(self.logical),
            "transpiled": self.transpiled,
        }
        if self.transpiled:
            out["optimization_level"] = self.optimization_level
            out["executed"] = circuit_counts(self.executable)
            out["layout"] = layout_of(self.executable)
            out["executed_qasm3"] = dumps_qasm3(self.executable)
        return out


def transpile_for(
    circuit: QuantumCircuit, backend: Any, optimization_level: int = 1
) -> QuantumCircuit:
    """Transpile with the preset pass manager a hardware run uses."""
    pm = generate_preset_pass_manager(backend=backend, optimization_level=optimization_level)
    return pm.run(circuit)


def prepare(
    circuit: QuantumCircuit,
    backend_name: str | None,
    optimization_level: int = 1,
    hardware_backend: Any | None = None,
) -> Prepared:
    """Make a circuit ready to run on a backend.

    A fake device and a real one get the same preset pass manager and optimization
    level, so a noisy run's layout and gate counts are those the device would get.
    The ideal simulator gets only the decomposition Aer needs; exact runs take the
    logical circuit as it is.
    """
    if not 0 <= optimization_level <= 3:
        raise BackendError(f"optimization_level must be 0 to 3, got {optimization_level}.")
    kind, name = classify(backend_name)
    if kind == "exact":
        return Prepared(kind, name, circuit, circuit, optimization_level)
    if kind == "aer":
        executable = transpile_for(circuit, ideal_simulator(), 0)
        return Prepared(kind, name, circuit, executable, optimization_level)
    target = fake_backend(name) if kind == "fake" else hardware_backend
    if target is None:
        raise BackendError(f"No device {name!r} to transpile for.")
    executable = transpile_for(circuit, target, optimization_level)
    return Prepared(kind, name, circuit, executable, optimization_level, transpiled=True)
