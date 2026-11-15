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

"""Hardware runs, on purpose and within the time left.

A hardware run needs an API key. Without one the tools answer with a typed result
(``reason: "key_needed"``) that says which values to set. Before any job is
submitted, the instance's remaining time is read; a job whose estimate exceeds it is
refused with both numbers and nothing is submitted. Nothing here creates an
instance, picks a plan or selects an instance other than the configured one.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .pubs import estimator_pub, sampler_pub


if TYPE_CHECKING:
    from .backends import Prepared
    from .pubs import Observables, ParameterSets


KEY_HELP = (
    "Create an API key at https://quantum.cloud.ibm.com (the key icon on the dashboard) "
    "and copy the Open Plan instance's CRN from the Instances page."
)
#: Seconds IBM counts for a job beyond its shots (loading, readout set-up).
JOB_OVERHEAD_SECONDS = 2.0
#: EstimatorV2's default precision; shots per observable group are 1/precision**2.
DEFAULT_ESTIMATOR_PRECISION = 0.015625


class HardwareRefusal(Exception):  # noqa: N818  (a refusal, not a fault)
    """A hardware call that cannot go ahead, with its typed reason."""

    def __init__(self, reason: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details

    def as_result(self) -> dict[str, Any]:
        return {"status": "error", "reason": self.reason, "message": self.message, **self.details}


def key_needed(action: str) -> HardwareRefusal:
    """The refusal for a hardware call made with no API key."""
    return HardwareRefusal(
        "key_needed",
        f"{action} needs an IBM Quantum API key, and none is set. Local backends "
        "(local_exact, local_aer, fake_<device>) need no key. " + KEY_HELP,
        needs=["token", "instance"],
    )


def service(action: str = "This call") -> Any:
    """The runtime service, from the configured key only."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    try:
        return ibm_runtime.initialize_service()
    except ibm_runtime.NoAccountKeyError as exc:
        raise key_needed(action) from exc
    except ValueError as exc:  # no saved credentials outside a host's switch
        raise key_needed(action) from exc


def has_key() -> bool:
    """Whether a key is configured, without contacting IBM."""
    from qiskit_ibm_runtime_mcp_server import ibm_runtime

    return ibm_runtime.get_token_from_env() is not None or ibm_runtime.service is not None


def device(svc: Any, name: str) -> Any:
    try:
        return svc.backend(name)
    except Exception as exc:
        raise HardwareRefusal(
            "backend_not_found",
            f"No IBM Quantum backend {name!r} on this instance: {exc}. "
            "List them with list_backends, or use a fake_<device> to simulate one.",
        ) from exc


def budget(svc: Any) -> dict[str, Any]:
    """The instance's time: limit, consumed and remaining seconds."""
    try:
        usage = dict(svc.usage())
    except Exception as exc:
        raise HardwareRefusal(
            "budget_unreadable",
            f"The instance's remaining time could not be read ({exc}), so nothing was submitted.",
        ) from exc
    limit = usage.get("usage_limit_seconds") or usage.get("usage_allocation_seconds")
    consumed = usage.get("usage_consumed_seconds") or 0
    remaining = usage.get("usage_remaining_seconds")
    if remaining is None and limit:
        remaining = max(float(limit) - float(consumed), 0.0)
    return {
        "limit_seconds": limit,
        "consumed_seconds": consumed,
        "remaining_seconds": remaining,
        "period": usage.get("usage_period"),
        "limit_reached": bool(usage.get("usage_limit_reached")),
    }


def per_shot_seconds(executable: Any, backend: Any) -> float:
    """The backend's repetition delay plus the circuit's own duration."""
    try:
        # Qiskit's configuration holds it in seconds; a raw backend file in microseconds.
        rep_delay = float(backend.configuration().default_rep_delay)
        if rep_delay > 1:
            rep_delay *= 1e-6
    except Exception:
        rep_delay = 250e-6
    duration = 0.0
    try:
        duration = float(executable.estimate_duration(backend.target, unit="s"))
    except Exception:
        duration = 0.0
    return rep_delay + duration


def estimate_seconds(
    primitive: str,
    prepared: Prepared,
    backend: Any,
    sets: ParameterSets,
    shots: int | None = None,
    num_observables: int = 1,
    precision: float | None = None,
    zne_factors: int = 1,
) -> dict[str, Any]:
    """Quantum seconds a job is expected to use: shots x circuits x per-shot time."""
    per_shot = per_shot_seconds(prepared.executable, backend)
    if primitive == "sampler":
        total_shots = sets.count * int(shots or 0)
    else:
        p = precision or DEFAULT_ESTIMATOR_PRECISION
        total_shots = sets.count * num_observables * math.ceil(1 / (p * p)) * max(zne_factors, 1)
    seconds = total_shots * per_shot + JOB_OVERHEAD_SECONDS
    return {
        "seconds": round(seconds, 3),
        "shots": total_shots,
        "per_shot_seconds": per_shot,
        "method": "shots x circuits x the backend's per-shot time, plus "
        f"{JOB_OVERHEAD_SECONDS:g} s for the job",
    }


def check_budget(estimate: dict[str, Any], time: dict[str, Any]) -> None:
    """Refuse a job that does not fit in the time left."""
    remaining = time.get("remaining_seconds")
    if remaining is None:
        raise HardwareRefusal(
            "budget_unmetered",
            "This instance reports no time limit, so there is no remaining time to check a "
            "job against. This server runs hardware jobs only on an instance with a time "
            "limit, such as the Open Plan, so that no run is paid for by accident.",
            estimate_seconds=estimate["seconds"],
        )
    if time.get("limit_reached") or estimate["seconds"] > float(remaining):
        raise HardwareRefusal(
            "budget_exceeded",
            f"This job is estimated at {estimate['seconds']:g} s of quantum time and "
            f"{float(remaining):g} s are left, so it was not submitted. Use fewer shots, "
            "fewer parameter sets, or a local backend.",
            estimate_seconds=estimate["seconds"],
            remaining_seconds=float(remaining),
        )


def submit_sampler(
    backend: Any,
    prepared: Prepared,
    sets: ParameterSets,
    shots: int,
    options: dict[str, Any],
) -> Any:
    from qiskit_ibm_runtime import SamplerV2

    sampler = SamplerV2(mode=backend)
    sampler.options.dynamical_decoupling.enable = options.get("dynamical_decoupling", True)
    if sampler.options.dynamical_decoupling.enable:
        sampler.options.dynamical_decoupling.sequence_type = options.get("dd_sequence", "XY4")
    sampler.options.twirling.enable_gates = options.get("twirling", True)
    sampler.options.twirling.enable_measure = options.get("measure_twirling", True)
    return sampler.run([sampler_pub(prepared.executable, sets)], shots=shots)


def submit_estimator(
    backend: Any,
    prepared: Prepared,
    obs: Observables,
    sets: ParameterSets,
    options: dict[str, Any],
) -> Any:
    from qiskit_ibm_runtime import EstimatorV2

    estimator = EstimatorV2(mode=backend)
    estimator.options.resilience_level = options.get("resilience_level", 1)
    if options.get("zne_mitigation") and estimator.options.resilience_level > 0:
        estimator.options.resilience.zne_mitigation = True
        if options.get("zne_noise_factors"):
            estimator.options.resilience.zne.noise_factors = tuple(options["zne_noise_factors"])
    if options.get("precision"):
        estimator.options.default_precision = options["precision"]
    pub = estimator_pub(prepared.executable, obs, sets, prepared.executable.layout)
    return estimator.run([pub])


def seconds_used(job: Any) -> float | None:
    try:
        return float(job.usage())
    except Exception:
        try:
            return float(job.metrics()["usage"]["quantum_seconds"])
        except Exception:
            return None
