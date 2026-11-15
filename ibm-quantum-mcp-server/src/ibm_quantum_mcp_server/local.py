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
    else:
        from qiskit_ibm_runtime import SamplerV2

        sampler = SamplerV2(mode=_mode(prepared))
        if seed is not None:
            sampler.options.simulator.seed_simulator = seed
        result = sampler.run([pub], shots=shots).result()
    return sampler_result(result[0], sets.as_lists())


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
