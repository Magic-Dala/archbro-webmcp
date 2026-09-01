from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator, model_validator

from archbro.backend.core.contracts import ArchitectureNodeKind
from archbro.backend.core.diagram_layout import layout_diagram


CODE_ARCHITECTURE_SCHEMA = "archbro.code_architecture.v1"
CODE_DIAGRAM_VERSION = "archbro.code_diagram.v1"
_FULL_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_GITHUB_REPOSITORY = re.compile(
    r"^(?:https://github\.com/)?(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class CodeSourceEvidence(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=500)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=4000)
    symbol: str | None = Field(default=None, max_length=240)
    blob_sha: str | None = Field(default=None, max_length=64)

    @field_validator("path")
    @classmethod
    def validate_repo_relative_path(cls, value: str) -> str:
        value = value.strip().replace("\\", "/")
        path = PurePosixPath(value)
        if (
            not value
            or value.startswith("/")
            or path.is_absolute()
            or re.match(r"^[A-Za-z]:/", value)
            or ":" in path.parts[0]
            or ".." in path.parts
            or ".git" in path.parts
        ):
            raise ValueError("source path must be a safe repository-relative POSIX path")
        return value

    @field_validator("blob_sha")
    @classmethod
    def normalize_blob_sha(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if value and not re.fullmatch(r"[0-9a-f]{40,64}", value):
            raise ValueError("blob_sha must be a 40-64 character hexadecimal digest when supplied")
        return value or None

    @model_validator(mode="after")
    def validate_line_evidence(self) -> "CodeSourceEvidence":
        if self.line_end < self.line_start:
            raise ValueError("source evidence line_end must be >= line_start")
        expected_lines = self.line_end - self.line_start + 1
        actual_lines = len(self.excerpt.splitlines())
        if actual_lines != expected_lines:
            raise ValueError(
                "source evidence excerpt line count must exactly match line_start..line_end"
            )
        return self


class CodeArchitectureComponent(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    type: str = Field(min_length=1, max_length=120)
    responsibility: str = Field(min_length=1, max_length=1000)
    kind: ArchitectureNodeKind = ArchitectureNodeKind.SYSTEM
    source_evidence_ids: list[str] = Field(min_length=1, max_length=12)
    children: list["CodeArchitectureComponent"] = Field(default_factory=list, max_length=7)


class CodeArchitectureRelationship(BaseModel):
    source: str = Field(min_length=1, max_length=160)
    target: str = Field(min_length=1, max_length=160)
    relationship_type: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    source_evidence_ids: list[str] = Field(min_length=1, max_length=12)


class CodeArchitectureSnapshotRequest(BaseModel):
    repository: str = Field(min_length=3, max_length=300)
    revision: str = Field(min_length=40, max_length=40)
    summary: str = Field(min_length=1, max_length=2000)
    components: list[CodeArchitectureComponent] = Field(min_length=1, max_length=8)
    relationships: list[CodeArchitectureRelationship] = Field(default_factory=list, max_length=120)
    source_evidence: list[CodeSourceEvidence] = Field(min_length=1, max_length=160)

    @field_validator("repository")
    @classmethod
    def normalize_github_repository(cls, value: str) -> str:
        value = value.strip()
        match = _GITHUB_REPOSITORY.fullmatch(value)
        if not match:
            raise ValueError("repository must be owner/repo or an https://github.com/owner/repo URL")
        return f"{match.group('owner')}/{match.group('repo')}"

    @field_validator("revision")
    @classmethod
    def require_full_revision(cls, value: str) -> str:
        value = value.strip().lower()
        if not _FULL_GIT_SHA.fullmatch(value):
            raise ValueError("revision must be an exact full 40-character Git commit SHA")
        return value

    @model_validator(mode="after")
    def validate_snapshot_graph(self) -> "CodeArchitectureSnapshotRequest":
        evidence_ids: set[str] = set()
        for evidence in self.source_evidence:
            if evidence.id in evidence_ids:
                raise ValueError(f"duplicate source evidence id: {evidence.id}")
            evidence_ids.add(evidence.id)

        component_ids: set[str] = set()
        total = 0

        def walk(nodes: list[CodeArchitectureComponent], depth: int) -> None:
            nonlocal total
            for node in nodes:
                total += 1
                if total > 40:
                    raise ValueError("code architecture allows at most 40 total nodes")
                if node.id in component_ids:
                    raise ValueError(f"duplicate code architecture component id: {node.id}")
                component_ids.add(node.id)
                unknown = set(node.source_evidence_ids) - evidence_ids
                if unknown:
                    raise ValueError(
                        f"component {node.id} references unknown source evidence: {sorted(unknown)}"
                    )
                if depth >= 3 and node.children:
                    raise ValueError("code architecture depth is capped at 3 levels")
                if depth == 2 and len(node.children) > 6:
                    raise ValueError("code architecture level-3 detail is capped at 6 children per node")
                walk(node.children, depth + 1)

        walk(self.components, 1)
        total_evidence_chars = sum(len(item.excerpt) for item in self.source_evidence)
        if total_evidence_chars > 200_000:
            raise ValueError("code architecture source evidence is capped at 200,000 characters")
        for relationship in self.relationships:
            if relationship.source not in component_ids or relationship.target not in component_ids:
                raise ValueError("code architecture relationships must reference existing component ids")
            unknown = set(relationship.source_evidence_ids) - evidence_ids
            if unknown:
                raise ValueError(
                    "relationship "
                    f"{relationship.source}->{relationship.target} references unknown source evidence: {sorted(unknown)}"
                )
        return self


def _source_href(repository: str, revision: str, evidence: CodeSourceEvidence) -> str:
    line_fragment = f"#L{evidence.line_start}"
    if evidence.line_end != evidence.line_start:
        line_fragment += f"-L{evidence.line_end}"
    encoded_path = "/".join(quote(segment, safe="") for segment in evidence.path.split("/"))
    return f"https://github.com/{repository}/blob/{revision}/{encoded_path}{line_fragment}"


def _edge_id(
    *,
    repository: str,
    revision: str,
    relationship: CodeArchitectureRelationship,
    occurrence: int,
) -> str:
    payload = "\x1f".join(
        [
            repository,
            revision,
            relationship.source,
            relationship.target,
            relationship.relationship_type,
            relationship.description,
            ",".join(sorted(relationship.source_evidence_ids)),
            str(occurrence),
        ]
    )
    return "code-edge:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def build_code_architecture_snapshot(
    project_id: str,
    request: CodeArchitectureSnapshotRequest,
) -> dict:
    evidence_by_id = {item.id: item for item in request.source_evidence}
    nodes: list[dict] = []

    def visit(
        components: list[CodeArchitectureComponent],
        *,
        parent_node_id: str | None,
        depth: int,
    ) -> None:
        for component in sorted(components, key=lambda item: item.id):
            node_id = f"code-node:{component.id}"
            sources = [
                {
                    **evidence_by_id[evidence_id].model_dump(mode="json"),
                    "href": _source_href(
                        request.repository,
                        request.revision,
                        evidence_by_id[evidence_id],
                    ),
                }
                for evidence_id in component.source_evidence_ids
            ]
            nodes.append(
                {
                    "id": node_id,
                    "component_id": component.id,
                    "semantic_kind": component.kind.value,
                    "semantic_type": component.type,
                    "label": component.name,
                    "responsibility": component.responsibility,
                    "parent_id": parent_node_id,
                    "depth": depth,
                    "source_evidence_ids": list(component.source_evidence_ids),
                    "sources": sources,
                    "child_count": len(component.children),
                }
            )
            visit(component.children, parent_node_id=node_id, depth=depth + 1)

    visit(request.components, parent_node_id=None, depth=1)

    occurrences: dict[tuple[str, str, str, str], int] = {}
    edges: list[dict] = []
    for relationship in sorted(
        request.relationships,
        key=lambda item: (
            item.source,
            item.target,
            item.relationship_type,
            item.description,
        ),
    ):
        key = (
            relationship.source,
            relationship.target,
            relationship.relationship_type,
            relationship.description,
        )
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        evidence = [
            {
                **evidence_by_id[evidence_id].model_dump(mode="json"),
                "href": _source_href(
                    request.repository,
                    request.revision,
                    evidence_by_id[evidence_id],
                ),
            }
            for evidence_id in relationship.source_evidence_ids
        ]
        edges.append(
            {
                "id": _edge_id(
                    repository=request.repository,
                    revision=request.revision,
                    relationship=relationship,
                    occurrence=occurrence,
                ),
                "source": f"code-node:{relationship.source}",
                "target": f"code-node:{relationship.target}",
                "semantic_type": relationship.relationship_type,
                "label": relationship.relationship_type,
                "supporting_text": relationship.description,
                "source_evidence_ids": list(relationship.source_evidence_ids),
                "sources": evidence,
            }
        )

    diagram = {
        "diagram_version": CODE_DIAGRAM_VERSION,
        "repository_revision": request.revision,
        "summary": request.summary,
        "nodes": nodes,
        "edges": edges,
    }
    positioned = layout_diagram(diagram)
    return {
        "schema": CODE_ARCHITECTURE_SCHEMA,
        "project_id": project_id,
        "classification": "IMPLEMENTATION_EVIDENCE",
        "canonical_state_mutated": False,
        "repository": {
            "provider": "github",
            "slug": request.repository,
            "url": f"https://github.com/{request.repository}",
            "revision": request.revision,
            "revision_pinned": True,
        },
        "evidence_verification": {
            "mode": "REVISION_PINNED_AGENT_SUPPLIED",
            "repository_checkout_verified": False,
            "note": (
                "Source excerpts are structurally validated and pinned to the supplied full commit SHA. "
                "ArchBro does not claim that the server independently fetched the repository; the agent "
                "must obtain these excerpts from the connected GitHub MCP at this exact revision."
            ),
        },
        "summary": request.summary,
        "source_evidence": [
            {
                **item.model_dump(mode="json"),
                "href": _source_href(request.repository, request.revision, item),
            }
            for item in request.source_evidence
        ],
        "diagram": diagram,
        "positioned_graph": {
            "layout_version": positioned.layout_version,
            "diagram_version": positioned.diagram_version,
            "architecture_version": positioned.architecture_version,
            "width": positioned.width,
            "height": positioned.height,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "x": node.x,
                    "y": node.y,
                    "width": node.width,
                    "height": node.height,
                    "layer": node.layer,
                    "order": node.order,
                    "parent_id": node.parent_id,
                    "hierarchy_path": list(node.hierarchy_path),
                }
                for node in positioned.nodes
            ],
            "edges": [
                {
                    "edge_id": edge.edge_id,
                    "source": edge.source,
                    "target": edge.target,
                    "points": [{"x": point.x, "y": point.y} for point in edge.points],
                    "routing": edge.routing,
                    "order": edge.order,
                }
                for edge in positioned.edges
            ],
            "stable_order": list(positioned.stable_order),
        },
    }
