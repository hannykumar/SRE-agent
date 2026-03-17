from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from ops.settings import get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class GitOpsExecutor:
    def __init__(self, repo_dir: str | None = None, base_branch: str | None = None, author: str | None = None) -> None:
        settings = get_settings()
        self.repo_dir = Path(repo_dir or settings.gitops_repo_dir)
        self.base_branch = base_branch or settings.gitops_base_branch
        self.author = author or settings.gitops_author
        self.repo_dir.mkdir(parents=True, exist_ok=True)

    def _manifest_path(self, action: Dict[str, Any], incident: Dict[str, Any]) -> Path:
        relative = str(action.get("manifest_path") or incident.get("gitops_manifest_path") or f"clusters/{incident['namespace']}/{incident['service']}.json")
        return self.repo_dir / relative

    def _load_manifest(self, manifest_path: Path, incident: Dict[str, Any]) -> Dict[str, Any]:
        if manifest_path.exists():
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": incident["service"], "namespace": incident["namespace"]},
            "spec": {"replicas": int(incident.get("pod_status", {}).get("replicas", 1) or 1)},
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def _write_change_record(
        self,
        run_id: str,
        action: Dict[str, Any],
        before_manifest: Dict[str, Any],
        after_manifest: Dict[str, Any],
        kind: str,
    ) -> Dict[str, Any]:
        change_dir = self.repo_dir / "_changes" / run_id / kind
        change_dir.mkdir(parents=True, exist_ok=True)
        before_path = change_dir / "before.json"
        after_path = change_dir / "after.json"
        meta_path = change_dir / "change.json"
        before_path.write_text(json.dumps(before_manifest, indent=2), encoding="utf-8")
        after_path.write_text(json.dumps(after_manifest, indent=2), encoding="utf-8")
        metadata = {
            "kind": kind,
            "run_id": run_id,
            "author": self.author,
            "base_branch": self.base_branch,
            "branch": f"sre-agent/{run_id}/{kind}",
            "manifest": str(action.get("manifest_path", "")),
            "created_at": _now(),
            "action": action,
        }
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        return {
            "branch": metadata["branch"],
            "change_dir": str(change_dir),
            "before_path": str(before_path),
            "after_path": str(after_path),
            "metadata_path": str(meta_path),
        }

    def apply_action(self, run_id: str, action: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
        manifest_path = self._manifest_path(action, incident)
        before_manifest = self._load_manifest(manifest_path, incident)
        after_manifest = json.loads(json.dumps(before_manifest))
        after_manifest.setdefault("spec", {})
        after_manifest["spec"]["replicas"] = int(action["replicas"])
        manifest_path.write_text(json.dumps(after_manifest, indent=2), encoding="utf-8")
        change_record = self._write_change_record(run_id, action, before_manifest, after_manifest, kind="apply")
        return {
            "status": "ok",
            "tool": "gitops_apply",
            "command": f"gitops apply {action['manifest_path']} replicas={action['replicas']}",
            "action": action,
            "result": {
                "manifest_path": str(manifest_path),
                "base_branch": self.base_branch,
                **change_record,
            },
        }

    def apply_rollback(self, run_id: str, rollback_action: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
        manifest_path = self._manifest_path(rollback_action, incident)
        before_manifest = self._load_manifest(manifest_path, incident)
        after_manifest = json.loads(json.dumps(before_manifest))
        after_manifest.setdefault("spec", {})
        after_manifest["spec"]["replicas"] = int(rollback_action["replicas"])
        manifest_path.write_text(json.dumps(after_manifest, indent=2), encoding="utf-8")
        change_record = self._write_change_record(run_id, rollback_action, before_manifest, after_manifest, kind="rollback")
        return {
            "status": "ok",
            "tool": "gitops_rollback",
            "command": f"gitops rollback {rollback_action['manifest_path']} replicas={rollback_action['replicas']}",
            "action": rollback_action,
            "result": {
                "manifest_path": str(manifest_path),
                "base_branch": self.base_branch,
                **change_record,
            },
        }
