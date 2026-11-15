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

"""Running circuits and reading jobs, local and hardware, under one job model."""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import TYPE_CHECKING, Any

from qiskit_mcp_server.circuit_serialization import load_circuit

from . import backends, hardware, jobs, local, pubs, results


if TYPE_CHECKING:
    from qiskit import QuantumCircuit


logger = logging.getLogger(__name__)


class RunError(ValueError):
    """A run that cannot start, with a typed reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message

    def as_result(self) -> dict[str, Any]:
        return {"status": "error", "reason": self.reason, "message": self.message}


@functools.lru_cache(maxsize=1)
def store() -> jobs.JobStore:
    s = jobs.JobStore()
    interrupted = s.settle_interrupted()
    if interrupted:
        logger.info("Marked %d local jobs from an earlier server as interrupted", interrupted)
    return s


@functools.lru_cache(maxsize=1)
def runner() -> jobs.LocalRunner:
    return jobs.LocalRunner(store())


def reset() -> None:
    """Forget the store and runner (tests point the state folder elsewhere)."""
    store.cache_clear()
    runner.cache_clear()


def parse_circuit(circuit: str, circuit_format: str = "auto") -> QuantumCircuit:
    loaded = load_circuit(circuit, circuit_format)  # type: ignore[arg-type]
    if loaded["status"] == "error":
        raise RunError("circuit_invalid", loaded["message"])
    return loaded["circuit"]


def _base_record(
    primitive: str,
    circuit: QuantumCircuit,
    kind: str,
    backend_name: str,
    label: str | None,
) -> dict[str, Any]:
    qasm = backends.dumps_qasm3(circuit)
    return {
        "primitive": primitive,
        "kind": "local" if kind != "hardware" else "hardware",
        "backend": backend_name,
        "backend_kind": kind,
        "label": label,
        "circuit_qasm3": qasm,
        "circuit_sha256": jobs.circuit_sha256(qasm),
        "created_at": jobs.now(),
    }


async def _wait(job_id: str, wait_seconds: float) -> None:
    if wait_seconds > 0:
        await asyncio.to_thread(runner().wait, job_id, wait_seconds)


def view_of(record: dict[str, Any]) -> dict[str, Any]:
    """A job as the tools return it."""
    out = {
        "status": "success" if record.get("status") != "ERROR" else "error",
        "job_id": record["job_id"],
        "job_status": record.get("status"),
        "kind": record.get("kind"),
        "primitive": record.get("primitive"),
        "backend": record.get("backend"),
        "backend_kind": record.get("backend_kind"),
        "label": record.get("label"),
        "created_at": record.get("created_at"),
        "finished_at": record.get("finished_at"),
    }
    executed = (record.get("circuit") or {}).get("executed")
    if executed:
        out["transpiled"] = {
            "two_qubit_gates": executed["two_qubit_gates"],
            "depth": executed["depth"],
            "layout": (record.get("circuit") or {}).get("layout"),
        }
    if record.get("error"):
        out["error"] = record["error"]
        out["message"] = record["error"].get("message")
    if record.get("budget"):
        out["budget"] = record["budget"]
    if record.get("result") is not None:
        out["result"] = record["result"]
    elif record.get("status") in jobs.ACTIVE:
        out["message"] = (
            f"The job is {str(record.get('status')).lower()}; read it with get_job_results."
        )
    return out


async def run_sampler(
    circuit: str,
    backend_name: str | None,
    shots: int,
    parameter_values: Any,
    circuit_format: str,
    optimization_level: int,
    seed: int | None,
    wait_seconds: float,
    label: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    if shots < 1:
        raise RunError("shots_invalid", "shots must be at least 1.")
    qc = parse_circuit(circuit, circuit_format)
    if not any(i.operation.name == "measure" for i in qc.data):
        raise RunError(
            "no_measurements",
            "The circuit measures nothing, so the Sampler has no bits to count. Add "
            "measurements (measure_all), or use run_estimator for expectation values.",
        )
    sets = _sets(qc, parameter_values)
    kind, name = _classify(backend_name)
    if kind == "hardware":
        return await asyncio.to_thread(
            _submit_hardware,
            "sampler",
            qc,
            name,
            sets,
            None,
            optimization_level,
            label,
            {**options, "shots": shots},
        )
    prepared = _prepare(qc, name, optimization_level)
    record = _base_record("sampler", qc, kind, name, label)
    record.update(
        job_id=jobs.new_local_id(),
        circuit=prepared.describe(),
        pubs={"parameter_values": sets.as_lists(), "batched": sets.batched},
        options={"shots": shots, "seed": seed, "optimization_level": optimization_level},
        shots=shots,
    )
    runner().submit(record, lambda: local.run_sampler(prepared, sets, shots, seed))
    await _wait(record["job_id"], wait_seconds)
    return view_of(store().get(record["job_id"]) or record)


async def run_estimator(
    circuit: str,
    observables: Any,
    backend_name: str | None,
    parameter_values: Any,
    circuit_format: str,
    optimization_level: int,
    precision: float | None,
    seed: int | None,
    wait_seconds: float,
    label: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    qc = parse_circuit(circuit, circuit_format)
    if qc.num_clbits:
        qc = qc.remove_final_measurements(inplace=False)
    try:
        obs = pubs.observables(observables, qc.num_qubits)
    except pubs.PubError as exc:
        raise RunError("observables_invalid", str(exc)) from exc
    sets = _sets(qc, parameter_values)
    kind, name = _classify(backend_name)
    if kind == "hardware":
        return await asyncio.to_thread(
            _submit_hardware,
            "estimator",
            qc,
            name,
            sets,
            obs,
            optimization_level,
            label,
            {**options, "precision": precision},
        )
    prepared = _prepare(qc, name, optimization_level)
    record = _base_record("estimator", qc, kind, name, label)
    record.update(
        job_id=jobs.new_local_id(),
        circuit=prepared.describe(),
        pubs={
            "parameter_values": sets.as_lists(),
            "batched": sets.batched,
            "observables": obs.labels,
        },
        options={"precision": precision, "seed": seed, "optimization_level": optimization_level},
    )
    runner().submit(record, lambda: local.run_estimator(prepared, obs, sets, precision, seed))
    await _wait(record["job_id"], wait_seconds)
    return view_of(store().get(record["job_id"]) or record)


def _sets(qc: QuantumCircuit, parameter_values: Any) -> pubs.ParameterSets:
    try:
        return pubs.parameter_sets(qc, parameter_values)
    except pubs.PubError as exc:
        raise RunError("parameters_invalid", str(exc)) from exc


def _classify(backend_name: str | None) -> tuple[backends.BackendKind, str]:
    try:
        return backends.classify(backend_name)
    except backends.BackendError as exc:
        raise RunError("backend_not_found", str(exc)) from exc


def _prepare(qc: QuantumCircuit, name: str, optimization_level: int) -> backends.Prepared:
    try:
        return backends.prepare(qc, name, optimization_level)
    except backends.BackendError as exc:
        raise RunError("backend_invalid", str(exc)) from exc


def _submit_hardware(
    primitive: str,
    qc: QuantumCircuit,
    name: str,
    sets: pubs.ParameterSets,
    obs: pubs.Observables | None,
    optimization_level: int,
    label: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Read the time left, then submit; or refuse with both numbers."""
    svc = hardware.service("A run on a real device")
    device = hardware.device(svc, name)
    prepared = backends.prepare(qc, name, optimization_level, hardware_backend=device)
    zne = 0
    if primitive == "estimator" and options.get("zne_mitigation"):
        zne = len(options.get("zne_noise_factors") or (1, 1.5, 2))
    estimate = hardware.estimate_seconds(
        primitive,
        prepared,
        device,
        sets,
        shots=options.get("shots"),
        num_observables=len(obs.labels) if obs else 1,
        precision=options.get("precision"),
        zne_factors=zne,
    )
    time_left = hardware.budget(svc)
    hardware.check_budget(estimate, time_left)
    if primitive == "sampler":
        job = hardware.submit_sampler(device, prepared, sets, int(options["shots"]), options)
    else:
        assert obs is not None  # nosec B101
        job = hardware.submit_estimator(device, prepared, obs, sets, options)
    record = _base_record(primitive, qc, "hardware", name, label)
    record.update(
        job_id=job.job_id(),
        status="QUEUED",
        circuit=prepared.describe(),
        pubs={
            "parameter_values": sets.as_lists(),
            "batched": sets.batched,
            **({"observables": obs.labels} if obs else {}),
        },
        options=options,
        shots=options.get("shots"),
        budget={
            "estimate_seconds": estimate["seconds"],
            "estimate_method": estimate["method"],
            "remaining_seconds_before": time_left["remaining_seconds"],
        },
    )
    store().save(record)
    out = view_of(record)
    out["message"] = (
        f"Submitted to {name}. Estimated {estimate['seconds']:g} s of the "
        f"{float(time_left['remaining_seconds'] or 0):g} s left. Read it with get_job_results; "
        "a real device's queue can take minutes to hours."
    )
    return out


# --- Reading jobs -------------------------------------------------------------------


def status_name(status: Any) -> str:
    """A job status as IBM names it (DONE), from a string or a JobStatus."""
    return str(getattr(status, "name", status)).upper()


def _hardware_job_record(job_id: str) -> dict[str, Any]:
    """Refresh a hardware job's record from IBM: status, and results once done."""
    svc = hardware.service("Reading a hardware job")
    try:
        job = svc.job(job_id)
    except Exception as exc:
        raise RunError("job_not_found", f"No job {job_id!r}: {exc}") from exc
    record = store().get(job_id) or {
        "job_id": job_id,
        "kind": "hardware",
        "backend": getattr(job.backend(), "name", None) if job.backend() else None,
        "backend_kind": "hardware",
        "primitive": getattr(job, "primitive_id", None),
        "created_at": str(getattr(job, "creation_date", "") or ""),
    }
    status = status_name(job.status())
    record["status"] = status
    if status == "DONE" and record.get("result") is None:
        result = job.result()
        pub = result[0]
        values = (record.get("pubs") or {}).get("parameter_values")
        if hasattr(pub.data, "evs"):
            labels = (record.get("pubs") or {}).get("observables") or ["observable"]
            record["result"] = results.estimator_result(pub, labels, values)
            record["primitive"] = "estimator"
        else:
            record["result"] = results.sampler_result(pub, values)
            record["primitive"] = "sampler"
        record["finished_at"] = jobs.now()
    if status in ("ERROR", "CANCELLED"):
        message = job.error_message() if hasattr(job, "error_message") else None
        record["error"] = {"reason": status.lower(), "message": message or f"Job {status}."}
    if status in jobs.FINAL:
        used = hardware.seconds_used(job)
        budget = dict(record.get("budget") or {})
        budget["seconds_used"] = used
        try:
            budget["remaining_seconds"] = hardware.budget(svc)["remaining_seconds"]
        except hardware.HardwareRefusal:
            budget["remaining_seconds"] = None
        record["budget"] = budget
    store().save(record)
    return record


def job_record(job_id: str) -> dict[str, Any]:
    if jobs.is_local_id(job_id):
        record = store().get(job_id)
        if record is None:
            raise RunError(
                "job_not_found", f"No local job {job_id!r}. List jobs with list_my_jobs."
            )
        return record
    return _hardware_job_record(job_id)


async def get_job(job_id: str) -> dict[str, Any]:
    record = await asyncio.to_thread(job_record, job_id)
    return view_of(record)


async def list_jobs(limit: int) -> dict[str, Any]:
    local_jobs = [view_of(r) for r in store().list()[: max(limit, 0)] if r.get("kind") == "local"]
    for job in local_jobs:
        job.pop("result", None)
    out: dict[str, Any] = {"status": "success", "local_jobs": local_jobs}
    if hardware.has_key():
        try:
            svc = await asyncio.to_thread(hardware.service, "Listing hardware jobs")
            listed = await asyncio.to_thread(svc.jobs, limit=limit)
            out["hardware_jobs"] = [
                {
                    "job_id": j.job_id(),
                    "kind": "hardware",
                    "job_status": status_name(j.status()),
                    "backend": j.backend().name if j.backend() else None,
                    "created_at": str(getattr(j, "creation_date", "") or ""),
                }
                for j in listed
            ]
        except hardware.HardwareRefusal as exc:
            out["hardware_jobs_note"] = exc.message
        except Exception as exc:
            out["hardware_jobs_note"] = f"Hardware jobs could not be listed: {exc}"
    else:
        out["hardware_jobs_note"] = "No API key is set, so only local jobs are listed."
    out["total_jobs"] = len(local_jobs) + len(out.get("hardware_jobs", []))
    return out


async def cancel(job_id: str) -> dict[str, Any]:
    if jobs.is_local_id(job_id):
        if store().get(job_id) is None:
            raise RunError("job_not_found", f"No local job {job_id!r}.")
        done = runner().cancel(job_id)
        record = store().get(job_id) or {}
        return {
            "status": "success" if done else "error",
            "job_id": job_id,
            "job_status": record.get("status"),
            "message": "Cancelled." if done else f"The job is already {record.get('status')}.",
        }

    def _cancel() -> dict[str, Any]:
        svc = hardware.service("Cancelling a hardware job")
        svc.job(job_id).cancel()
        return {"status": "success", "job_id": job_id, "message": "Cancellation requested."}

    return await asyncio.to_thread(_cancel)


async def compare(
    job_ids: list[str],
    labels: list[str] | None,
    expected: list[int] | None,
    threshold: float,
    positive_above: bool,
) -> dict[str, Any]:
    records = []
    for i, job_id in enumerate(job_ids):
        record = await asyncio.to_thread(job_record, job_id)
        if record.get("result") is None:
            raise RunError(
                "job_not_done",
                f"Job {job_id} is {record.get('status')}; compare it once it is DONE.",
            )
        if labels and i < len(labels) and labels[i]:
            record = {**record, "label": labels[i]}
        records.append(record)
    try:
        out = results.compare(records, expected, threshold, positive_above)
    except ValueError as exc:
        raise RunError("jobs_not_comparable", str(exc)) from exc
    out["status"] = "success"
    return out


def record(
    circuit: str,
    primitive: str,
    backend_name: str,
    counts: Any,
    values: Any,
    standard_errors: Any,
    parameter_values: Any,
    observables: Any,
    label: str | None,
    circuit_format: str,
) -> dict[str, Any]:
    """Keep a job run elsewhere (run_code, the SDK on a simulator) under a local id."""
    qc = parse_circuit(circuit, circuit_format)
    values_sets = None
    if parameter_values is not None:
        values_sets = _sets(qc, parameter_values).as_lists()
    if primitive == "sampler":
        if counts is None:
            raise RunError("result_missing", "A Sampler job's record needs counts.")
        per_set = counts if isinstance(counts, list) else [counts]
        result: dict[str, Any] = {
            "primitive": "sampler",
            "num_sets": len(per_set),
            "shots": int(sum(per_set[0].values())) if per_set else 0,
            "sets": [
                {
                    "index": i,
                    "parameter_values": values_sets[i] if values_sets else None,
                    "counts": {str(k): int(v) for k, v in sorted(c.items())},
                    "shots": int(sum(c.values())),
                    "quasi_probabilities": {
                        str(k): int(v) / max(sum(c.values()), 1) for k, v in sorted(c.items())
                    },
                }
                for i, c in enumerate(per_set)
            ],
        }
    elif primitive == "estimator":
        if values is None:
            raise RunError("result_missing", "An Estimator job's record needs values.")
        rows = [v if isinstance(v, list) else [v] for v in values]
        errs = standard_errors or [[None] * len(r) for r in rows]
        errs = [e if isinstance(e, list) else [e] for e in errs]
        labels = observables if isinstance(observables, list) else [observables or "observable"]
        result = {
            "primitive": "estimator",
            "observables": [str(o) for o in labels],
            "num_sets": len(rows),
            "sets": [
                {
                    "index": i,
                    "parameter_values": values_sets[i] if values_sets else None,
                    "values": [float(x) for x in row],
                    "standard_errors": [None if e is None else float(e) for e in errs[i]],
                }
                for i, row in enumerate(rows)
            ],
        }
    else:
        raise RunError("primitive_invalid", "primitive is 'sampler' or 'estimator'.")
    try:
        kind, name = backends.classify(backend_name)
    except backends.BackendError:
        kind, name = "hardware", backend_name
    # A recorded job ran outside this server; a device name keeps its name, not a kind
    # this server can vouch for.
    rec = _base_record(primitive, qc, kind if kind != "hardware" else "recorded", name, label)
    rec.update(
        job_id=jobs.new_local_id(),
        origin="recorded",
        status="DONE",
        finished_at=jobs.now(),
        circuit={"logical": backends.circuit_counts(qc), "transpiled": False},
        pubs={"parameter_values": values_sets, "observables": result.get("observables")},
        result=result,
    )
    store().save(rec)
    return view_of(rec)
