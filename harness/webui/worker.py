"""Single-host queue worker that supervises the existing MoA CLI."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from harness.scripts.attachments import AttachmentError, prepare_attachment_context
from harness.scripts import config as harness_config

from .monitoring import OperationalError, capture_operational_error
from .store import Store


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
PHASE_PROGRESS = {"layer1": 0.08, "layer2": 0.43, "layer3": 0.78}


def _process_tree(root_pid: int) -> list[int]:
    """Return descendants before their parent is signalled (Linux /proc)."""
    parent_for: dict[int, int] = {}
    proc_root = Path("/proc")
    if proc_root.is_dir():
        for stat_path in proc_root.glob("[0-9]*/stat"):
            try:
                # comm may contain spaces/parentheses, so split after the last ).
                rest = stat_path.read_text(errors="replace").rsplit(")", 1)[1].split()
                parent_for[int(stat_path.parent.name)] = int(rest[1])
            except (OSError, ValueError, IndexError):
                continue
    found = [root_pid]
    index = 0
    while index < len(found):
        parent = found[index]
        found.extend(pid for pid, ppid in parent_for.items() if ppid == parent)
        index += 1
    return found


def _terminate_tree(root_pid: int) -> None:
    pids = _process_tree(root_pid)
    # Children first because adapters intentionally create their own sessions.
    for pid in reversed(pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


class JobWorker:
    def __init__(self, store: Store, runner: Path, poll_seconds: float = 0.5):
        self.store = store
        self.runner = runner
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="moa-web-worker", daemon=True
        )
        self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=3)

    def cancel(self, job_id: str) -> bool:
        job = self.store.get_job(job_id)
        if not job or job["status"] not in {"queued", "running", "cancelling"}:
            return False
        changed = self.store.request_cancel(job_id)
        if not changed:
            return False
        if job["status"] == "queued":
            self.store.update_job(
                job_id,
                status="cancelled",
                phase="cancelled",
                finished_at=time.time(),
            )
            self.store.append_event(job_id, "job", "Queued job cancelled")
            return True
        self.store.update_job(job_id, status="cancelling")
        self.store.append_event(job_id, "warning", "Cancellation requested")
        with self._lock:
            proc = self._processes.get(job_id)
        if proc and proc.poll() is None:
            _terminate_tree(proc.pid)
        self.wake()
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next_job()
            if job:
                self._run_job(job)
                continue
            self._wake.wait(self.poll_seconds)
            self._wake.clear()

    def _event(self, job_id: str, kind: str, message: str, **data: Any) -> None:
        clean = ANSI_RE.sub("", message).replace("\x00", "")
        self.store.append_event(job_id, kind, clean[:8000], data)

    def _command(self, job: dict[str, Any], phase: str) -> list[str]:
        config = job["config"]
        cmd = [
            sys.executable,
            "-u",
            str(self.runner),
            "--scout-brief",
            str(Path(job["session_dir"]) / "scout-brief.json"),
            "--repo",
            job["workspace"],
            "--phase",
            phase,
        ]
        proposers = config.get("proposers") or []
        refiners = config.get("refiners") or []
        aggregator = config.get("aggregator")
        options = config.get("options") or {}
        if proposers:
            cmd.extend(["--proposers", ",".join(proposers)])
        if refiners:
            cmd.extend(["--refiners", ",".join(refiners)])
        if aggregator:
            cmd.extend(["--aggregator-provider", aggregator])
        if options.get("timeout"):
            cmd.extend(["--timeout", str(int(options["timeout"]))])
        if options.get("skip_layer2"):
            cmd.append("--skip-layer2")
        if options.get("no_report"):
            cmd.append("--no-report")
        retry = config.get("redispatch") or {}
        if retry.get("phase") == phase and retry.get("agents"):
            cmd.extend(["--redispatch", ",".join(retry["agents"])])
        return cmd

    @staticmethod
    def _environment(job: dict[str, Any]) -> dict[str, str]:
        config = job["config"]
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        provider_catalog = harness_config.load_provider_catalog()
        for provider_name, model in (
            config.get("options", {}).get("model_overrides") or {}
        ).items():
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(provider_name)):
                key = "MOA_" + str(provider_name).upper().replace("-", "_") + "_MODEL"
                env[key] = str(model)[:300]
        for provider_name, effort in (
            config.get("options", {}).get("effort_overrides") or {}
        ).items():
            provider = provider_catalog.get(str(provider_name))
            # AGY depth is selected by the model suffix, not --effort. Never
            # let an old browser payload create a conflicting CLI invocation.
            if provider and provider.harness == "agy":
                continue
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", str(provider_name))
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}", str(effort))
            ):
                key = "MOA_" + str(provider_name).upper().replace("-", "_") + "_EFFORT"
                env[key] = str(effort)
        return env

    def _run_phase(self, job: dict[str, Any], phase: str) -> int:
        job_id = job["id"]
        config = job["config"]
        log_path = Path(job["session_dir"]) / "webui.log"
        cmd = self._command(job, phase)
        self.store.update_job(
            job_id, phase=phase, progress=PHASE_PROGRESS[phase]
        )
        self._event(job_id, "phase", f"Starting {phase}", phase=phase)
        env = self._environment(job)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n$ {' '.join(cmd)}\n")
            proc = subprocess.Popen(
                cmd,
                cwd=job["workspace"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
            with self._lock:
                self._processes[job_id] = proc
            self.store.update_job(job_id, pid=proc.pid)
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                clean = ANSI_RE.sub("", line.rstrip())
                log.write(clean + "\n")
                log.flush()
                if clean:
                    self._event(job_id, "log", clean, phase=phase)
                if self.store.cancel_requested(job_id) and proc.poll() is None:
                    _terminate_tree(proc.pid)
            code = proc.wait()
            with self._lock:
                self._processes.pop(job_id, None)
            self.store.update_job(job_id, pid=None)
        self._event(
            job_id,
            "phase",
            f"{phase} {'finished' if code == 0 else 'failed'}",
            phase=phase,
            exit_code=code,
        )
        return code

    def _prepare_attachments(self, job: dict[str, Any]) -> None:
        """Build attachment context in the worker so progress is observable."""
        job_id = job["id"]
        session_dir = Path(job["session_dir"])
        scout_path = session_dir / "scout-brief.json"
        if not scout_path.is_file():
            return
        scout = json.loads(scout_path.read_text(encoding="utf-8"))
        uploads = scout.get("uploaded_files") or []
        if not uploads:
            return

        self.store.update_job(job_id, phase="attachments", progress=0.03)
        self._event(
            job_id,
            "attachment-progress",
            f"Preparing {len(uploads)} reference {'file' if len(uploads) == 1 else 'files'}",
            file_count=len(uploads),
            stage="starting",
        )

        def report(update: dict[str, Any]) -> None:
            file_index = int(update.get("file_index") or 1)
            file_count = max(int(update.get("file_count") or len(uploads)), 1)
            page_number = int(update.get("page_number") or 0)
            page_count = int(update.get("page_count") or 0)
            ocr_page_count = int(update.get("ocr_page_count") or 0)
            completed_pages = int(update.get("completed_pages") or 0)
            stage = str(update.get("stage") or "preparing")
            if ocr_page_count:
                if stage == "extracting":
                    in_file = 0.1 * page_number / max(page_count, 1)
                else:
                    in_file = 0.1 + 0.9 * completed_pages / ocr_page_count
                completed = (file_index - 1) + min(in_file, 1)
            elif page_count:
                completed = (file_index - 1) + 0.1 * page_number / page_count
            else:
                completed = file_index - 1 + (1 if stage == "complete" else 0)
            self.store.update_job(
                job_id,
                phase="attachments",
                progress=0.03 + 0.05 * min(completed / file_count, 1),
            )
            name = str(update.get("file_name") or "reference file")
            if ocr_page_count:
                worker_count = int(update.get("worker_count") or 1)
                if stage == "ocr-starting":
                    message = (
                        f"Starting parallel OCR for {name}: {ocr_page_count} pages "
                        f"across {worker_count} workers"
                    )
                elif stage == "ocr-complete":
                    message = (
                        f"OCRed {name}: {completed_pages} of "
                        f"{ocr_page_count} pages complete"
                    )
                else:
                    verb = "Rendering" if stage == "rendering" else "OCRing"
                    message = (
                        f"{verb} {name}: page {page_number}; "
                        f"{completed_pages} of {ocr_page_count} complete"
                    )
            elif page_count:
                verb = {
                    "extracting": "Checking",
                    "complete": "Completed",
                }.get(stage, "Preparing")
                message = f"{verb} {name}: page {page_number} of {page_count}"
            else:
                message = (
                    f"Completed {name}"
                    if stage == "complete"
                    else f"Preparing {name} ({file_index} of {file_count})"
                )
            self._event(job_id, "attachment-progress", message, **update)

        try:
            prepare_attachment_context(
                scout,
                session_dir,
                progress=report,
                cancelled=lambda: self.store.cancel_requested(job_id),
            )
        except AttachmentError:
            raise
        scout_path.write_text(json.dumps(scout, indent=2), encoding="utf-8")
        context = scout["attachment_context"]
        self.store.update_job(job_id, progress=0.08)
        self._event(
            job_id,
            "attachment",
            (
                f"Prepared {context['source_count']} reference "
                f"{'file' if context['source_count'] == 1 else 'files'} as shared text context"
            ),
            source_count=context["source_count"],
            characters=context["characters"],
            sources=context["sources"],
        )

    @staticmethod
    def _transient_agents(job: dict[str, Any], phase: str) -> list[str]:
        session_dir = Path(job["session_dir"])
        manifest_path = (
            session_dir / "layer1-manifest.json"
            if phase == "layer1"
            else session_dir / "manifest.json"
        )
        summary_key = (
            "transient_empty_proposers"
            if phase == "layer1"
            else "transient_empty_refiners"
        )
        roster_key = "proposers" if phase == "layer1" else "refiners"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        allowed = set(job["config"].get(roster_key) or [])
        return [
            name
            for name in manifest.get("summary", {}).get(summary_key, [])
            if name in allowed
        ]

    def _retry_transient_once(
        self,
        job: dict[str, Any],
        phase: str,
    ) -> int | None:
        agents = self._transient_agents(job, phase)
        if not agents:
            return None
        self._event(
            job["id"],
            "warning",
            f"Retrying incomplete {phase} output once: {', '.join(agents)}",
            phase=phase,
            agents=agents,
        )
        config = job["config"]
        previous_retry = config.get("redispatch")
        config["redispatch"] = {"phase": phase, "agents": agents}
        try:
            return self._run_phase(job, phase)
        finally:
            if previous_retry is None:
                config.pop("redispatch", None)
            else:
                config["redispatch"] = previous_retry

    @staticmethod
    def _manifest_diagnostic(
        job: dict[str, Any], phase: str
    ) -> dict[str, Any] | None:
        """Summarize per-agent rejection causes after a completed phase.

        The CLI intentionally returns zero for a Layer 1 bridge even when no
        proposer survived validation, because an interactive parent may choose
        to redispatch. The Web UI is autonomous, so it needs to inspect the
        bridge before blindly starting Layer 2 and turn the manifest into an
        actionable, reader-friendly event.
        """
        session_dir = Path(job["session_dir"])
        manifest_path = (
            session_dir / "layer1-manifest.json"
            if phase == "layer1"
            else session_dir / "manifest.json"
        )
        layer_key = "layer1" if phase == "layer1" else "layer2"
        role = "proposer" if phase == "layer1" else "refiner"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        results = [
            item for item in manifest.get(layer_key, []) if isinstance(item, dict)
        ]
        if not results and phase == "layer1":
            expected = [
                str(name) for name in job.get("config", {}).get("proposers") or []
            ]
            if expected:
                message = (
                    f"0 of {len(expected)} proposer results accepted; the "
                    "proposal checkpoint contained no agent result records. "
                    "Review the worker log, then use Redispatch failures; "
                    "review cannot start without at least one accepted proposal."
                )
                return {
                    "message": message,
                    "phase": phase,
                    "accepted": 0,
                    "rejected": len(expected),
                    "failed_agents": expected,
                    "workspace_mutation_agents": [],
                    "transient_agents": [],
                }
        failures = [item for item in results if not item.get("success")]
        if not results or not failures:
            return None
        successful = len(results) - len(failures)
        mutation_agents = [
            str(item.get("agent_id") or "unknown")
            for item in failures
            if item.get("workspace_mutations")
        ]
        transient_agents = [
            str(item.get("agent_id") or "unknown")
            for item in failures
            if item.get("transient_empty")
        ]
        failed_agents = [str(item.get("agent_id") or "unknown") for item in failures]

        reasons: list[str] = []
        if mutation_agents:
            reasons.append(
                "repository files changed during analysis "
                f"({', '.join(mutation_agents)})"
            )
        if transient_agents:
            reasons.append(
                "incomplete model output "
                f"({', '.join(transient_agents)})"
            )
        other_agents = [
            agent
            for agent in failed_agents
            if agent not in mutation_agents and agent not in transient_agents
        ]
        if other_agents:
            reasons.append(f"provider or validation errors ({', '.join(other_agents)})")
        reason_text = "; ".join(reasons)
        message = (
            f"{successful} of {len(results)} {role} results accepted; "
            f"{len(failures)} rejected because {reason_text}."
        )
        if successful == 0 and phase == "layer1":
            message += (
                " Review the rejected lanes, then use Redispatch failures; "
                "review cannot start without at least one accepted proposal."
            )
        return {
            "message": message,
            "phase": phase,
            "accepted": successful,
            "rejected": len(failures),
            "failed_agents": failed_agents,
            "workspace_mutation_agents": mutation_agents,
            "transient_agents": transient_agents,
        }

    def _run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self._event(job_id, "job", "Job started")
        code = 1
        try:
            self._prepare_attachments(job)
            options = job["config"].get("options") or {}
            retry = job["config"].get("redispatch") or {}
            retry_phase = retry.get("phase")
            phases = [retry_phase] if retry_phase in {"layer1", "layer2"} else ["layer1"]
            if "layer1" in phases:
                phases.append("layer2")
            if options.get("aggregate", True):
                phases.append("layer3")
            for phase in phases:
                if self.store.cancel_requested(job_id):
                    raise InterruptedError
                code = self._run_phase(job, phase)
                if code != 0:
                    break
                if phase in {"layer1", "layer2"}:
                    retry_code = self._retry_transient_once(job, phase)
                    if retry_code is not None:
                        code = retry_code
                        if code != 0:
                            break
                    diagnostic = self._manifest_diagnostic(job, phase)
                    if diagnostic:
                        self._event(
                            job_id,
                            "warning",
                            diagnostic["message"],
                            **{
                                key: value
                                for key, value in diagnostic.items()
                                if key != "message"
                            },
                        )
                        if phase == "layer1" and diagnostic["accepted"] == 0:
                            code = 4
                            self.store.update_job(
                                job_id, summary=diagnostic["message"]
                            )
                            break
            if self.store.cancel_requested(job_id):
                raise InterruptedError
            if code == 0:
                summary = _artifact_summary(Path(job["session_dir"]))
                self.store.update_job(
                    job_id,
                    status="completed",
                    phase="complete",
                    progress=1,
                    summary=summary,
                    exit_code=0,
                    finished_at=time.time(),
                )
                self._event(job_id, "job", "Job completed", summary=summary)
            else:
                self.store.update_job(
                    job_id,
                    status="failed",
                    phase="failed",
                    exit_code=code,
                    finished_at=time.time(),
                )
                self._event(job_id, "error", f"Job failed with exit code {code}")
                capture_operational_error(
                    OperationalError(
                        f"MoA run {job_id} failed in {phase} with exit code {code}"
                    ),
                    operation="worker.run_failed",
                    context={
                        "exit_code": code,
                        "job_id": job_id,
                        "phase": phase,
                    },
                )
        except InterruptedError:
            self.store.update_job(
                job_id,
                status="cancelled",
                phase="cancelled",
                exit_code=-signal.SIGTERM,
                finished_at=time.time(),
            )
            self._event(job_id, "job", "Job cancelled")
        except AttachmentError as exc:
            self.store.update_job(
                job_id,
                status="failed",
                phase="failed",
                summary=str(exc),
                exit_code=1,
                finished_at=time.time(),
            )
            self._event(job_id, "error", f"Reference preparation failed: {exc}")
        except Exception as exc:
            self.store.update_job(
                job_id,
                status="failed",
                phase="failed",
                summary=str(exc),
                exit_code=1,
                finished_at=time.time(),
            )
            self._event(job_id, "error", f"Worker error: {exc}")
            capture_operational_error(
                exc,
                operation="worker.unexpected_exception",
                context={
                    "job_id": job_id,
                    "phase": (self.store.get_job(job_id) or {}).get("phase"),
                },
            )


def _artifact_summary(session_dir: Path) -> str:
    final_plan = session_dir / "final-plan.md"
    if final_plan.exists():
        text = final_plan.read_text(encoding="utf-8", errors="replace").strip()
        for line in text.splitlines():
            if line.strip().lstrip("#").strip():
                return line.strip().lstrip("#").strip()[:500]
    manifest = session_dir / "manifest.json"
    if manifest.exists():
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
            good1 = sum(bool(item.get("success")) for item in doc.get("layer1", []))
            good2 = sum(bool(item.get("success")) for item in doc.get("layer2", []))
            return f"Completed with {good1} proposer and {good2} refiner results."
        except (OSError, json.JSONDecodeError):
            pass
    return "Run completed."
