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

"""Primitive unified blocs (PUBs) from a tool's arguments.

One call is one PUB, so a batch of inputs is one job: ``parameter_values`` is one
parameter set or a list of sets, and the Estimator's ``observables`` is one
observable or a list of them. Every set is measured against every observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
from qiskit.quantum_info import SparsePauliOp


if TYPE_CHECKING:
    from qiskit import QuantumCircuit


class PubError(ValueError):
    """Arguments that do not make a PUB for this circuit."""


@dataclass
class ParameterSets:
    """A circuit's parameter sets as a ``(sets, parameters)`` array."""

    values: np.ndarray | None
    batched: bool

    @property
    def count(self) -> int:
        return 1 if self.values is None else int(self.values.shape[0])

    def as_lists(self) -> list[list[float]] | None:
        return None if self.values is None else self.values.tolist()


def parameter_sets(circuit: QuantumCircuit, parameter_values: Any) -> ParameterSets:
    """Read ``parameter_values`` as one set (a list of numbers) or many (a list of lists).

    Values are in the order of ``circuit.parameters``, which Qiskit sorts by name.
    """
    n = circuit.num_parameters
    if parameter_values is None or (isinstance(parameter_values, list) and not parameter_values):
        if n:
            raise PubError(
                f"The circuit has {n} parameters ({_names(circuit)}); give parameter_values."
            )
        return ParameterSets(None, False)
    if not isinstance(parameter_values, list):
        raise PubError("parameter_values is a list of numbers, or a list of such lists.")
    batched = isinstance(parameter_values[0], list)
    rows = parameter_values if batched else [parameter_values]
    try:
        values = np.asarray(rows, dtype=float)
    except (TypeError, ValueError) as exc:
        raise PubError(f"parameter_values must hold numbers only: {exc}") from exc
    if values.ndim != 2:
        raise PubError("Every parameter set must be a flat list of numbers of the same length.")
    if values.shape[1] != n:
        raise PubError(
            f"Each parameter set needs {n} values, in the order {_names(circuit)}; "
            f"got {values.shape[1]}."
        )
    if not n:
        return ParameterSets(None, False)
    return ParameterSets(values, batched)


def _names(circuit: QuantumCircuit) -> str:
    names = [p.name for p in circuit.parameters]
    return ", ".join(names[:12]) + (", …" if len(names) > 12 else "")


@dataclass
class Observables:
    """Observables as Pauli sums, with the labels the results use."""

    operators: list[SparsePauliOp]
    labels: list[str]
    single: bool


def observables(raw: Any, num_qubits: int) -> Observables:
    """Read ``observables``.

    - ``"ZZ"``: one Pauli string.
    - ``["ZZ", "XI"]``: several observables, one value each.
    - ``[["ZZ", 0.5], ["XX", -0.3]]``: one weighted sum.
    - ``[[["ZZ", 0.5], ["XX", -0.3]], "ZI"]``: several, mixing the forms.

    Pauli strings follow Qiskit's order: the rightmost character is qubit 0.
    """
    if isinstance(raw, str):
        items: list[Any] = [raw]
        single = True
    elif isinstance(raw, list) and raw and _is_weighted_sum(raw):
        items = [raw]
        single = True
    elif isinstance(raw, list) and raw:
        items = raw
        single = False
    else:
        raise PubError("observables is a Pauli string, a weighted sum, or a list of them.")
    operators = [_operator(item) for item in items]
    for op, item in zip(operators, items, strict=True):
        if op.num_qubits != num_qubits:
            raise PubError(
                f"Observable {_label(item)} acts on {op.num_qubits} qubits; "
                f"the circuit has {num_qubits}."
            )
    return Observables(operators, [_label(item) for item in items], single)


def _is_weighted_sum(raw: list[Any]) -> bool:
    return all(
        isinstance(term, (list, tuple))
        and len(term) == 2
        and isinstance(term[0], str)
        and isinstance(term[1], (int, float))
        for term in raw
    )


def _operator(item: Any) -> SparsePauliOp:
    try:
        if isinstance(item, str):
            return SparsePauliOp(item)
        if isinstance(item, (list, tuple)) and _is_weighted_sum(list(item)):
            return SparsePauliOp.from_list([(str(p), complex(c)) for p, c in item])
    except Exception as exc:  # Qiskit's own message names the bad label
        raise PubError(f"Observable {_label(item)} is not a Pauli observable: {exc}") from exc
    raise PubError(f"Observable {_label(item)} is not a Pauli string or a weighted sum.")


def _label(item: Any) -> str:
    if isinstance(item, str):
        return item
    try:
        return " + ".join(f"{float(c):g}·{p}" for p, c in item)
    except Exception:
        return repr(item)


def estimator_pub(
    circuit: QuantumCircuit, obs: Observables, sets: ParameterSets, layout: Any | None = None
) -> tuple[Any, ...]:
    """An Estimator PUB whose values come back as ``(sets, observables)``."""
    operators = [op.apply_layout(layout) if layout is not None else op for op in obs.operators]
    grid = [operators]
    if sets.values is None:
        return (circuit, grid)
    return (circuit, grid, sets.values[:, np.newaxis, :])


def sampler_pub(circuit: QuantumCircuit, sets: ParameterSets) -> tuple[Any, ...]:
    """A Sampler PUB whose counts come back one per set."""
    if sets.values is None:
        return (circuit,)
    return (circuit, sets.values)
