import os
import re
import tempfile
from pathlib import Path
from typing import Any

from horus.core.models import Model
from horus.core.service import Service
from horus.core.store import HorusError, atomic_write, digest, file_lock

KINDS = {"sources", "configs", "plans", "bundles", "reviews", "artifacts"}


class ReviewPackage(Model):
    schemaVersion: str = "1"
    projectRef: str
    name: str
    planRef: str
    reviewRef: str
    assets: dict[str, dict[str, Any]]
    bundleHash: str


def export_review(service: Service, project_ref: str, review_ref: str) -> dict[str, Any]:
    store = service.project(project_ref)
    review = store.get("reviews", review_ref)
    service.review_gate(project_ref, review["planRef"], review_ref, review["environment"])
    plan = store.get("plans", review["planRef"])
    bundle = store.get("bundles", review["reviewBundleRef"])
    references = {
        "plans": [review["planRef"]],
        "reviews": [review_ref],
        "configs": [review["configRef"]],
        "bundles": [review["reviewBundleRef"]],
        "artifacts": [bundle["artifactRef"]],
        "sources": plan["sourceRefs"],
    }
    payload = {
        "schemaVersion": "1",
        "projectRef": project_ref,
        "name": service.open_project(project_ref)["name"],
        "planRef": review["planRef"],
        "reviewRef": review_ref,
        "assets": {
            kind: {ref: store.get(kind, ref) for ref in refs} for kind, refs in references.items()
        },
    }
    return {**payload, "bundleHash": digest(payload)}


def import_review(service: Service, raw: dict[str, Any]) -> dict[str, Any]:
    package = ReviewPackage.model_validate(raw)
    payload = package.model_dump(exclude={"bundleHash"})
    if (
        package.schemaVersion != "1"
        or digest(payload) != package.bundleHash
        or set(package.assets) != KINDS
        or not re.fullmatch(r"project_[0-9a-f]{32}", package.projectRef)
    ):
        raise HorusError("bundle_integrity")
    with tempfile.TemporaryDirectory(prefix=".import-", dir=service.workspace) as temporary:
        staging = Service(Path(temporary))
        staged_root = staging.workspace / "projects" / package.projectRef
        atomic_write(
            staged_root / "project.json", {"name": package.name, "projectRef": package.projectRef}
        )
        store = staging.project(package.projectRef)
        for kind, entries in package.assets.items():
            for ref, value in entries.items():
                if store.put(kind, value) != ref:
                    raise HorusError("bundle_integrity", ref)
        review = store.get("reviews", package.reviewRef)
        staging.review_gate(
            package.projectRef, package.planRef, package.reviewRef, review["environment"]
        )
        with file_lock(service.workspace / ".projects.lock"):
            for existing in service.list_projects():
                if (
                    existing["name"] == package.name
                    and existing["projectRef"] != package.projectRef
                ):
                    raise HorusError("project_name_conflict")
            destination = service.workspace / "projects" / package.projectRef
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                target = service.project(package.projectRef)
                for kind, entries in package.assets.items():
                    for value in entries.values():
                        target.put(kind, value)
            else:
                os.rename(staged_root, destination)
    return {
        "projectRef": package.projectRef,
        "planRef": package.planRef,
        "reviewRef": package.reviewRef,
        "status": "imported",
    }
