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

"""Running a prepared PUB on this machine.

Exact runs use Qiskit's statevector primitives. Ideal and noisy shots use
qiskit-ibm-runtime's own SamplerV2 and EstimatorV2 in local mode, on an
``AerSimulator`` (with the fake device's noise model for ``fake_<name>``), so a
local run goes through the same primitive code a hardware run does.
"""

from __future__ import annotations

from typing import Any

from .backends import Prepared, ideal_simulator, noisy_simulator
from .pubs import Observables, ParameterSets, estimator_pub, sampler_pub
from .results import estimator_result, sampler_result


def _mode(prepared: Prepared) -> Any:
    if prepared.kind == "fake":
        return noisy_simulator(prepared.backend_name)
    return ideal_simulator()


def run_sampler(
    prepared: Prepared, sets: ParameterSets, shots: int, seed: int | None = None
) -> dict[str, Any]:
    """Sample a circuit locally and return counts per parameter set."""
    pub = sampler_pub(prepared.executable, sets)
    if prepared.kind == "exact":
        from qiskit.primitives import StatevectorSampler

        result = StatevectorSampler(seed=seed).run([pub], shots=shots).result()
        out = sampler_result(result[0], sets.as_lists())
        listed = sets.as_lists()
        rows: list[list[float] | None] = list(listed) if listed else [None]
        for entry, row in zip(out["sets"], rows, strict=False):
            exact = exact_distribution(prepared.logical, row)
            if exact is not None:
                entry["exact_probabilities"] = exact
        return out
    else:
        from qiskit_ibm_runtime import SamplerV2

        sampler = SamplerV2(mode=_mode(prepared))
        if seed is not None:
            sampler.options.simulator.seed_simulator = seed
        result = sampler.run([pub], shots=shots).result()
    return sampler_result(result[0], sets.as_lists())


def exact_distribution(circuit: Any, values: list[float] | None) -> dict[str, float] | None:
    """The exact probability of each bitstring a measured circuit gives, in the counts'
    bit order; None when its measurements are not final, cover not every bit, or use more
    than one register."""
    from qiskit.quantum_info import Statevector

    if len(circuit.cregs) != 1:
        return None
    pairs = [
        (circuit.find_bit(i.clbits[0]).index, circuit.find_bit(i.qubits[0]).index)
        for i in circuit.data
        if i.operation.name == "measure"
    ]
    if sorted(c for c, _ in pairs) != list(range(circuit.num_clbits)):
        return None
    try:
        bare = circuit.remove_final_measurements(inplace=False)
    except Exception:
        return None
    if any(i.operation.name == "measure" for i in bare.data):
        return None
    if values is not None:
        bare = bare.assign_parameters(values)
    qubits = [q for _, q in sorted(pairs)]
    probabilities = Statevector(bare).probabilities_dict(qargs=qubits)
    return {k: float(v) for k, v in sorted(probabilities.items()) if v > 1e-12}


def run_estimator(
    prepared: Prepared,
    obs: Observables,
    sets: ParameterSets,
    precision: float | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Estimate expectation values locally, per parameter set and observable."""
    layout = prepared.executable.layout if prepared.transpiled else None
    pub = estimator_pub(prepared.executable, obs, sets, layout)
    if prepared.kind == "exact":
        from qiskit.primitives import StatevectorEstimator

        result = StatevectorEstimator(seed=seed).run([pub]).result()
    else:
        from qiskit_ibm_runtime import EstimatorV2

        estimator = EstimatorV2(mode=_mode(prepared))
        if seed is not None:
            estimator.options.simulator.seed_simulator = seed
        if precision is not None:
            estimator.options.default_precision = precision
        result = estimator.run([pub]).result()
    return estimator_result(result[0], obs.labels, sets.as_lists())
