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

"""Results of Sampler and Estimator jobs, and comparisons between jobs.

A result is one entry per parameter set, so a batch of inputs reads as a list:
counts and quasi-probabilities for the Sampler, expectation values with their
standard errors for the Estimator.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def sampler_result(pub_result: Any, parameter_values: list[list[float]] | None) -> dict[str, Any]:
    """Counts per parameter set from one SamplerV2 PUB result."""
    joined = pub_result.join_data()
    shape = tuple(joined.shape)
    n_sets = int(np.prod(shape)) if shape else 1
    sets = []
    for index in range(n_sets):
        counts = joined.get_counts(loc=index) if shape else joined.get_counts()
        shots = int(sum(counts.values()))
        sets.append(
            {
                "index": index,
                "parameter_values": parameter_values[index] if parameter_values else None,
                "counts": dict(sorted(counts.items())),
                "shots": shots,
                "quasi_probabilities": {k: v / shots for k, v in sorted(counts.items()) if shots},
            }
        )
    return {
        "primitive": "sampler",
        "num_sets": n_sets,
        "shots": sets[0]["shots"] if sets else 0,
        "sets": sets,
    }


def estimator_result(
    pub_result: Any, labels: list[str], parameter_values: list[list[float]] | None
) -> dict[str, Any]:
    """Expectation values per parameter set from one EstimatorV2 PUB result.

    The PUB is built so values come back as ``(sets, observables)``.
    """
    evs = np.asarray(pub_result.data.evs, dtype=float).reshape(-1, len(labels))
    stds = np.asarray(pub_result.data.stds, dtype=float).reshape(-1, len(labels))
    sets = [
        {
            "index": i,
            "parameter_values": parameter_values[i] if parameter_values else None,
            "values": [_finite(v) for v in evs[i]],
            "standard_errors": [_finite(v) for v in stds[i]],
        }
        for i in range(evs.shape[0])
    ]
    return {
        "primitive": "estimator",
        "observables": labels,
        "num_sets": len(sets),
        "sets": sets,
    }


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def total_variation_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Half the L1 distance between two count distributions, from 0 to 1."""
    total_a = sum(a.values()) or 1
    total_b = sum(b.values()) or 1
    keys = set(a) | set(b)
    return 0.5 * sum(abs(a.get(k, 0) / total_a - b.get(k, 0) / total_b) for k in keys)


def predictions(
    values: list[float | None], threshold: float = 0.0, positive_above: bool = True
) -> list[int | None]:
    """Class 1 where a value is on the positive side of the threshold, else 0."""
    out: list[int | None] = []
    for value in values:
        if value is None:
            out.append(None)
        else:
            out.append(int(value >= threshold) if positive_above else int(value < threshold))
    return out


def accuracy(predicted: list[int | None], expected: list[int]) -> float | None:
    """The share of sets whose predicted class matches the expected label."""
    pairs = [(p, _label01(e)) for p, e in zip(predicted, expected, strict=False) if p is not None]
    if not pairs:
        return None
    return sum(1 for p, e in pairs if p == e) / len(pairs)


def _label01(label: int) -> int:
    """Labels may be 0/1 or -1/+1; -1 reads as class 0."""
    return 1 if label > 0 else 0


def compare(
    jobs: list[dict[str, Any]],
    expected: list[int] | None = None,
    threshold: float = 0.0,
    positive_above: bool = True,
) -> dict[str, Any]:
    """Aligned results for jobs that ran the same circuit on different backends.

    Each job is a record with ``job_id``, ``backend``, ``backend_kind`` and ``result``.
    The first job is the reference the others are measured against.
    """
    if not jobs:
        raise ValueError("Give at least one job.")
    primitives = {job["result"]["primitive"] for job in jobs}
    if len(primitives) != 1:
        raise ValueError(
            "These jobs ran different primitives (" + ", ".join(sorted(primitives)) + "); "
            "compare Sampler jobs with Sampler jobs and Estimator jobs with Estimator jobs."
        )
    circuits = {job.get("circuit_sha256") for job in jobs if job.get("circuit_sha256")}
    head = [
        {
            "job_id": job["job_id"],
            "backend": job["backend"],
            "backend_kind": job["backend_kind"],
            "label": job.get("label") or job["backend"],
        }
        for job in jobs
    ]
    out: dict[str, Any] = {
        "primitive": primitives.pop(),
        "jobs": head,
        "same_circuit": len(circuits) <= 1,
    }
    if out["primitive"] == "sampler":
        out.update(_compare_sampler(jobs))
    else:
        out.update(_compare_estimator(jobs, expected, threshold, positive_above))
    return out


def _compare_sampler(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    n_sets = min(job["result"]["num_sets"] for job in jobs)
    sets = []
    for index in range(n_sets):
        # An exact job is compared by its exact probabilities, not by its sampled shots.
        dists = [
            job["result"]["sets"][index].get("exact_probabilities")
            or job["result"]["sets"][index]["counts"]
            for job in jobs
        ]
        keys = sorted(set().union(*dists))
        rows = []
        for key in keys:
            row: dict[str, Any] = {"bitstring": key}
            for job, dist in zip(jobs, dists, strict=True):
                total = sum(dist.values()) or 1
                row[job["job_id"]] = dist.get(key, 0) / total
            rows.append(row)
        sets.append(
            {
                "index": index,
                "bitstrings": keys,
                "probabilities": rows,
                "total_variation_distance": {
                    job["job_id"]: total_variation_distance(dists[0], dist)
                    for job, dist in zip(jobs[1:], dists[1:], strict=True)
                },
            }
        )
    return {"num_sets": n_sets, "sets": sets}


def _compare_estimator(
    jobs: list[dict[str, Any]],
    expected: list[int] | None,
    threshold: float,
    positive_above: bool,
) -> dict[str, Any]:
    labels = jobs[0]["result"]["observables"]
    n_sets = min(job["result"]["num_sets"] for job in jobs)
    sets = []
    for index in range(n_sets):
        per_job = {
            job["job_id"]: {
                "values": job["result"]["sets"][index]["values"],
                "standard_errors": job["result"]["sets"][index]["standard_errors"],
            }
            for job in jobs
        }
        ref = jobs[0]["result"]["sets"][index]["values"]
        entry: dict[str, Any] = {
            "index": index,
            "parameter_values": jobs[0]["result"]["sets"][index].get("parameter_values"),
            "jobs": per_job,
            "difference_from_reference": {
                job["job_id"]: [
                    None if a is None or b is None else b - a
                    for a, b in zip(ref, job["result"]["sets"][index]["values"], strict=True)
                ]
                for job in jobs[1:]
            },
        }
        if expected is not None and index < len(expected):
            entry["expected"] = expected[index]
        sets.append(entry)
    out: dict[str, Any] = {"observables": labels, "num_sets": n_sets, "sets": sets}
    if expected is not None:
        acc: dict[str, Any] = {}
        flips: dict[str, list[int]] = {}
        ref_pred = None
        for job in jobs:
            first = [s["values"][0] for s in job["result"]["sets"][:n_sets]]
            pred = predictions(first, threshold, positive_above)
            acc[job["job_id"]] = accuracy(pred, expected[:n_sets])
            if ref_pred is None:
                ref_pred = pred
            else:
                flips[job["job_id"]] = [
                    i for i, (a, b) in enumerate(zip(ref_pred, pred, strict=True)) if a != b
                ]
        out["classification"] = {
            "observable": labels[0],
            "threshold": threshold,
            "positive_above": positive_above,
            "accuracy": acc,
            "flipped_from_reference": flips,
        }
    return out
