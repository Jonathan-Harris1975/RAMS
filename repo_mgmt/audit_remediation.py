"""Durable audit work dispatch and exact-commit completion reconciliation.

RAMS does not edit target repositories. A trusted target-repository workflow
owns the provider handoff; native branch/PR automation owns publication/merge.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from typing import Any, Callable
from urllib.parse import quote

import httpx

from repo_mgmt.github_pr import parse_github_repo

_LOCK = threading.RLock()
_SHA = re.compile(r"[0-9a-f]{40}")
_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_REQUIRED = {
    "website": {"Production readiness", "CodeQL", "Security and repository quality", "Item 6 hardening"},
    "content": {"AIMS CI", "CodeQL", "Security and repository quality", "Item 6 hardening"},
}
_DEPLOYMENT = {"website": "Website deployed integration", "content": "Koyeb production deployment watch"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def prefix(cfg: Any, pipeline: str, run_id: str) -> str:
    if pipeline not in _REQUIRED or not _ID.fullmatch(run_id):
        raise ValueError("invalid audit work identity")
    return f"{cfg.rms_report_prefix}/remediation/{pipeline}/{run_id}"


def protected(path: str) -> bool:
    parts = path.lower().split("/")
    return (path.startswith("/") or ".." in parts or not path or "\\" in path
            or parts[0] in {".github", ".git", "infra", "migrations"}
            or any(p in {"secrets", "credentials", "authentication"} for p in parts)
            or parts[-1] in {"package.json", "package-lock.json", "pyproject.toml", "requirements.in", "requirements-dev.txt"}
            or parts[-1].startswith(".env") or parts[-1].endswith((".lock", ".env", ".sql")))


class AuditRemediation:
    def __init__(self, cfg: Any, r2: Any, github: Callable[..., Any] | None = None, live_revision: Callable[..., bool] | None = None):
        self.cfg, self.r2 = cfg, r2
        self.github = github or self._github
        self.live_revision = live_revision or self._live_revision

    def _github(self, method: str, path: str, body: Any = None) -> Any:
        # All paths originate in configured repository identities, never audit URLs.
        with httpx.Client(timeout=self.cfg.rms_github_api_timeout_seconds, follow_redirects=False) as client:
            response = client.request(method, f"{self.cfg.rms_github_api_base.rstrip('/')}{path}", json=body,
                                      headers={"Authorization": f"Bearer {self.cfg.github_token_value}",
                                               "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
            response.raise_for_status()
            return response.json() if response.content else None

    def read(self, key: str) -> dict[str, Any]:
        value = json.loads(self.r2.get_object_limited(self.cfg.r2_bucket_audits, key, self.cfg.rms_report_max_bytes))
        if not isinstance(value, dict):
            raise ValueError("invalid audit work record")
        return value

    def write(self, key: str, value: dict[str, Any], *, create: bool = False) -> bool:
        body = json.dumps(value, sort_keys=True, allow_nan=False).encode()
        if len(body) > self.cfg.rms_report_max_bytes:
            raise ValueError("audit work record exceeds storage budget")
        if create:
            return self.r2.put_object_if_absent(self.cfg.r2_bucket_audits, key, body)
        self.r2.put_object(self.cfg.r2_bucket_audits, key, body, content_type="application/json")
        return True

    def register(self, pipeline: str, run_id: str, audit: dict[str, Any], tasks: list[dict[str, Any]]) -> dict[str, Any]:
        with _LOCK:
            root = prefix(self.cfg, pipeline, run_id)
            repository = "/".join(parse_github_repo(self.cfg.repo_url_for(pipeline)))
            eligible, blocked, guidance = [], [], []
            for task in tasks:
                row = dict(task)
                if row.get("status") == "routing_required" and row.get("proposedClassification") == "code_fix":
                    paths = row.get("affectedPaths") or []
                    if (paths and row.get("sourceFindingIds") and row.get("requiredOutcome")
                            and row.get("acceptanceCriterion") and row.get("verificationMethod")
                            and all(isinstance(p, str) and not protected(p) for p in paths)):
                        eligible.append(row)
                    else:
                        blocked.append({**row, "remediationBlocker": "exact traceable non-protected scope required"})
                elif row.get("classification") == "future_guidance":
                    guidance.append(row)
                else:
                    blocked.append(row)
            batches = []
            for offset in range(0, len(eligible), 5):
                batch_tasks = eligible[offset:offset + 5]
                batch_id = digest({"repository": repository, "audit": audit.get("sourceAuditKey"), "tasks": batch_tasks})
                batches.append({"id": batch_id, "status": "pending", "tasks": batch_tasks,
                                "branch": f"fix/rams-audit-{batch_id[:24]}"})
            record = {"schemaVersion": "audit-remediation-run/v1", "pipeline": pipeline, "runId": run_id,
                      "repository": repository, "auditJsonKey": audit.get("sourceAuditKey"),
                      "auditDigest": digest({k: v for k, v in audit.items() if k != "sourceAuditKey"}),
                      "createdAt": time.time(), "updatedAt": time.time(), "batches": batches,
                      "manualReview": blocked, "futureGuidance": guidance,
                      "status": "pending" if batches else ("attention_required" if blocked else "completed"),
                      "completionBasis": "no actionable findings" if not batches and not blocked else None}
            key = f"{root}/state.json"
            if not self.write(key, record, create=True):
                return self.read(key)
            # Index is also durable; recovery can resume after a service restart.
            self.write(f"{self.cfg.rms_report_prefix}/remediation/active/{pipeline}-{run_id}.json",
                       {"stateKey": key, "pipeline": pipeline, "runId": run_id}, create=True)
            return self.reconcile(pipeline, run_id)

    def reconcile(self, pipeline: str, run_id: str) -> dict[str, Any]:
        with _LOCK:
            root = prefix(self.cfg, pipeline, run_id)
            state = self.read(f"{root}/state.json")
            if state.get("status") in {"completed", "attention_required"} and not any(
                    b.get("status") != "verified" for b in state.get("batches", [])):
                self._publish_progress(state, root)
                self.r2.delete_object(self.cfg.r2_bucket_audits,
                                      f"{self.cfg.rms_report_prefix}/remediation/active/{pipeline}-{run_id}.json")
                return state
            for batch in state["batches"]:
                if batch["status"] == "verified":
                    continue
                self._batch(state, batch, root)
                break  # one active audit writer per repository, one batch at a time
            if all(b["status"] == "verified" for b in state["batches"]):
                state["status"] = "attention_required" if state["manualReview"] else "completed"
                state["completionBasis"] = "exact-head tests, scoped regression tests, native merge and production verification"
            state["updatedAt"] = time.time()
            self.write(f"{root}/state.json", state)
            self._publish_progress(state, root)
            return state

    def _batch(self, state: dict[str, Any], batch: dict[str, Any], root: str) -> None:
        repository, pipeline = state["repository"], state["pipeline"]
        if batch["status"] == "pending":
            base = self.github("GET", f"/repos/{repository}/commits/{quote(self.cfg.repo_branch_for(pipeline), safe='')}")
            sha = base.get("sha", "")
            if not _SHA.fullmatch(sha):
                raise ValueError("target repository exact source SHA unavailable")
            plan_key = f"{root}/groups/{batch['id']}.json"
            test_path = (f"test/rams-audit-{batch['id'][:24]}.test.js" if pipeline == "content"
                         else f"tests/test_rams_audit_{batch['id'][:24]}.py")
            plan = {"schemaVersion": "audit-remediation-task/v1", "repository": repository,
                    "pipeline": pipeline, "runId": state["runId"], "batchId": batch["id"],
                    "auditJsonKey": state["auditJsonKey"], "auditDigest": state["auditDigest"],
                    "sourceSha": sha, "baseBranch": self.cfg.repo_branch_for(pipeline),
                    "branch": batch["branch"], "regressionTestPath": test_path, "tasks": batch["tasks"],
                    "stateKey": f"{root}/state.json", "planKey": plan_key}
            if not self.write(plan_key, plan, create=True):
                plan = self.read(plan_key)
            batch.update({"planKey": plan_key, "sourceSha": plan["sourceSha"], "regressionTestPath": plan["regressionTestPath"],
                          "status": "dispatch_pending", "dispatchRequestedAt": time.time()})
            # Persist before an external event. Duplicate repository events are
            # fenced by the target worker's create-once provider reservation.
            self.write(f"{root}/state.json", state)
            self.github("POST", f"/repos/{repository}/dispatches", {"event_type": "rams-audit-remediation",
                         "client_payload": {"plan_key": plan_key, "repository": repository}})
        plan = self.read(batch["planKey"])
        receipt_key = batch["planKey"].removesuffix(".json") + "/dispatch.json"
        try:
            receipt = self.read(receipt_key)
        except Exception:
            receipt = None
        if receipt and receipt.get("status") == "stale" and batch.get("replanAttempts", 0) < 3:
            # A stale-before-handoff receipt is retryable only when no external
            # provider reservation exists. Accepted/uncertain writes stay fenced.
            reservation_key = batch["planKey"].removesuffix(".json") + "/provider-reservation.json"
            if not self.r2.object_exists(self.cfg.r2_bucket_audits, reservation_key):
                attempts = batch.get("replanAttempts", 0) + 1
                new_id = digest({"previousBatch": batch["id"], "replanAttempt": attempts})
                previous = {"id": batch["id"], "planKey": batch["planKey"], "reason": "stale before provider handoff"}
                history = [*batch.get("previousPlans", []), previous]
                tasks = batch["tasks"]
                batch.clear()
                batch.update({"id": new_id, "status": "pending", "tasks": tasks,
                              "branch": f"fix/rams-audit-{new_id[:24]}", "replanAttempts": attempts, "previousPlans": history})
                state["status"] = "pending"
                return
        if receipt and receipt.get("status") in {"rejected", "blocked", "stale"}:
            batch["status"], state["status"] = "attention_required", "attention_required"
            batch["blocker"] = receipt.get("reason") or "provider dispatch rejected"
            return
        if receipt:
            batch["status"] = "repairing"
        elif time.time() - batch["dispatchRequestedAt"] > 300:
            attempts = batch.get("eventAttempts", 1)
            if attempts >= 5:
                state["status"], batch["blocker"] = "attention_required", "target workflow did not acknowledge five bounded dispatch events"
                return
            batch["eventAttempts"], batch["dispatchRequestedAt"] = attempts + 1, time.time()
            self.write(f"{root}/state.json", state)
            self.github("POST", f"/repos/{repository}/dispatches", {"event_type": "rams-audit-remediation",
                         "client_payload": {"plan_key": batch["planKey"], "repository": repository}})
        head = quote(repository.split('/')[0] + ':' + batch['branch'], safe='')
        prs = self.github("GET", f"/repos/{repository}/pulls?state=all&head={head}&per_page=10")
        if not isinstance(prs, list) or len(prs) > 1:
            raise ValueError("ambiguous audit implementation PR")
        if not prs:
            if time.time() - state["createdAt"] > 24 * 3600:
                state["status"] = "attention_required"
                batch["blocker"] = "No verified implementation PR; provider handoff must be reconciled before retry"
            return
        pr = self.github("GET", f"/repos/{repository}/pulls/{prs[0]['number']}")
        if (pr.get("head", {}).get("ref") != batch["branch"] or pr.get("base", {}).get("ref") != plan["baseBranch"]
                or pr.get("head", {}).get("repo", {}).get("full_name") != repository):
            raise ValueError("audit implementation identity mismatch")
        batch["pullRequest"] = {"number": pr["number"], "url": pr.get("html_url"), "headSha": pr["head"]["sha"]}
        if not pr.get("merged"):
            state["status"] = "running"
            batch["status"] = "awaiting_merge"
            if pr.get("state") == "closed":
                state["status"], batch["blocker"] = "attention_required", "implementation PR closed without merge"
            return
        files = self.github("GET", f"/repos/{repository}/pulls/{pr['number']}/files?per_page=100")
        if not isinstance(files, list) or len(files) >= 100:
            raise ValueError("unbounded audit implementation file set")
        paths = {p for t in batch["tasks"] for p in t["affectedPaths"]} | {plan["regressionTestPath"]}
        if plan["regressionTestPath"] not in {f.get("filename") for f in files} or any(f.get("filename") not in paths for f in files):
            state["status"], batch["blocker"] = "attention_required", "implementation scope or regression-test evidence missing"
            return
        if not self._checks(repository, pr["head"]["sha"], _REQUIRED[pipeline]):
            batch["status"] = "awaiting_checks"
            return
        merged_sha = pr.get("merge_commit_sha", "")
        if not _SHA.fullmatch(merged_sha):
            raise ValueError("merged SHA missing")
        if not self._checks(repository, merged_sha, {_DEPLOYMENT[pipeline]}) or not self.live_revision(repository, pipeline, merged_sha):
            batch["status"] = "awaiting_production_verification"
            return
        batch.update({"status": "verified", "mergeSha": merged_sha, "verifiedAt": time.time(),
                      "verification": {"sourceHeadChecks": sorted(_REQUIRED[pipeline]), "productionCheck": _DEPLOYMENT[pipeline],
                                       "regressionTestPath": plan["regressionTestPath"]}})
        self.write(batch["planKey"].removesuffix(".json") + "/verified.json",
                   {"schemaVersion": "audit-remediation-verification/v1", "repository": repository,
                    "batchId": batch["id"], "mergeSha": merged_sha, "verifiedAt": time.time()}, create=True)

    def _publish_progress(self, state: dict[str, Any], root: str) -> None:
        if state.get("status") == "completed":
            self.write(f"{root}/completion.json", {
                "schemaVersion": "audit-remediation-completion/v1", "pipeline": state["pipeline"],
                "runId": state["runId"], "repository": state["repository"], "auditJsonKey": state["auditJsonKey"],
                "completedAt": time.time(), "batches": state["batches"], "manualReview": [],
            }, create=True)
        key = f"{self.cfg.rms_report_prefix}/{state['pipeline']}/{state['runId']}/report.json"
        try:
            report = self.read(key)
        except Exception:
            return  # initial registration precedes publication of the processing report
        report["remediation"] = state
        report["reportQuality"] = {
            **report.get("reportQuality", {}), "status": state["status"],
            "manualInterventionRequired": state["status"] == "attention_required",
            "operatorNextStep": "No outstanding mandatory repair work." if state["status"] == "completed"
                else "Follow the durable repair ledger; report processing alone is not completion.",
        }
        self.write(key, report)
        latest_key = f"{self.cfg.rms_report_prefix}/{state['pipeline']}/latest.json"
        try:
            latest = self.read(latest_key)
        except Exception:
            return
        if latest.get("runId") == state["runId"]:
            self.write(latest_key, report)

    def _live_revision(self, repository: str, pipeline: str, sha: str) -> bool:
        if pipeline == "website":
            # Fixed governed origin; an audit/model URL cannot choose this probe.
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                response = client.get("https://jonathan-harris.online/release.json")
                response.raise_for_status()
                if len(response.content) > 65536:
                    return False
                return response.json().get("commit_sha") == sha
        deployments = self.github("GET", f"/repos/{repository}/deployments?per_page=100")
        if not isinstance(deployments, list):
            return False
        production = [d for d in deployments if d.get("environment") == "production-zeroth-kara/aims"
                      and d.get("creator", {}).get("login") == "koyeb[bot]"]
        if not production:
            return False
        latest = max(production, key=lambda d: int(d.get("id", 0)))
        if latest.get("sha") != sha:
            return False
        statuses = self.github("GET", f"/repos/{repository}/deployments/{latest['id']}/statuses?per_page=10")
        return bool(statuses) and statuses[0].get("state") == "success"

    def _checks(self, repository: str, sha: str, names: set[str]) -> bool:
        response = self.github("GET", f"/repos/{repository}/actions/runs?head_sha={sha}&per_page=100")
        runs = response.get("workflow_runs") if isinstance(response, dict) else None
        if not isinstance(runs, list) or len(runs) >= 100:
            return False
        latest: dict[str, Any] = {}
        for run in runs:
            if (run.get("name") in names and run.get("head_sha") == sha
                    and run.get("event") in {"pull_request", "push", "workflow_run", "workflow_dispatch"}):
                previous = latest.get(run["name"])
                if previous is None or run.get("id", 0) > previous.get("id", 0):
                    latest[run["name"]] = run
        return set(latest) == names and all(r.get("status") == "completed" and r.get("conclusion") == "success" for r in latest.values())

    def sweep(self) -> int:
        active = 0
        index = f"{self.cfg.rms_report_prefix}/remediation/active/"
        keys = self.r2.list_objects(self.cfg.r2_bucket_audits, index, max_keys=1000)
        if len(keys) >= 1000:
            raise ValueError("audit active index exceeds bounded sweep; refusing idle certification")
        for key in keys:
            item = self.read(key)
            try:
                state = self.reconcile(item["pipeline"], item["runId"])
                if any(b.get("status") != "verified" for b in state.get("batches", [])):
                    active += 1
            except Exception:
                # Uncertain work stays active and cannot authorise power-down.
                active += 1
                continue
        return active
