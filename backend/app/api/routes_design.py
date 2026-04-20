"""Design plane routes — AI-powered workflow creation (§13).

POST /api/design/rag_search          — Semantic search for tools + playbooks
POST /api/design/generate_workflow   — Generate playbook spec from intent
POST /api/design/validate_workflow   — Validate a workflow spec
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.services.architect_client import ArchitectClient
from app.services.embedding_service import EmbeddingService
from app.services.vector_search_service import VectorSearchService
from app.stores import postgres_store as store

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class RAGSearchRequest(BaseModel):
    query: str
    search_types: list[str] = ["tools", "playbooks"]
    limit: int = 10


class RAGSearchResponse(BaseModel):
    tools: list[dict] = []
    playbooks: list[dict] = []


class GenerateWorkflowRequest(BaseModel):
    intent: str
    context: dict | None = None  # {tools, example_playbooks, policies}
    target_incident_types: list[str] | None = None


class GenerateWorkflowResponse(BaseModel):
    workflow_spec: dict
    warnings: list[str] = []
    suggested_name: str | None = None
    suggested_description: str | None = None


class ValidateWorkflowRequest(BaseModel):
    workflow_spec: dict


class ValidateWorkflowResponse(BaseModel):
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/rag_search", response_model=RAGSearchResponse)
async def rag_search(
    body: RAGSearchRequest,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Semantic search for tools and playbooks (§13.1)."""
    embedding_svc = EmbeddingService()
    search_svc = VectorSearchService(embedding_svc)
    result = RAGSearchResponse()

    if "tools" in body.search_types:
        result.tools = await search_svc.search_tools(
            session, body.query, limit=body.limit, min_similarity=0.3,
        )

    if "playbooks" in body.search_types:
        result.playbooks = await search_svc.search_playbooks(
            session, body.query, limit=body.limit, min_similarity=0.3,
            status_filter=["published", "approved"],
        )

    return result


@router.post("/generate_workflow", response_model=GenerateWorkflowResponse)
async def generate_workflow(
    body: GenerateWorkflowRequest,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_role("editor")),
):
    """Generate a playbook workflow spec from natural language (§13.2)."""
    architect = ArchitectClient()

    # If no context provided, auto-fetch via RAG search
    if body.context is None:
        embedding_svc = EmbeddingService()
        search_svc = VectorSearchService(embedding_svc)
        tools = await search_svc.search_tools(
            session, body.intent, limit=10, min_similarity=0.3,
        )
        playbooks = await search_svc.search_playbooks(
            session, body.intent, limit=3, min_similarity=0.3,
            status_filter=["published"],
        )
        context_tools = tools
        context_playbooks = playbooks
    else:
        context_tools = body.context.get("tools", [])
        context_playbooks = body.context.get("example_playbooks", [])

    policies = (body.context or {}).get("policies", [])

    try:
        spec = await architect.generate_workflow(
            intent=body.intent,
            tools=context_tools,
            example_playbooks=context_playbooks if context_playbooks else None,
            policies=policies if policies else None,
            target_incident_types=body.target_incident_types,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Architect service error: {e}",
        )

    # Extract suggested name/description from generated spec
    warnings: list[str] = []
    suggested_name = spec.get("name")
    suggested_description = spec.get("description")

    # Run basic validation
    validation = await _validate_spec(spec, session)
    warnings.extend(validation["warnings"])

    return GenerateWorkflowResponse(
        workflow_spec=spec,
        warnings=warnings,
        suggested_name=suggested_name,
        suggested_description=suggested_description,
    )


@router.post("/validate_workflow", response_model=ValidateWorkflowResponse)
async def validate_workflow(
    body: ValidateWorkflowRequest,
    session: AsyncSession = Depends(get_db),
    _user: dict = Depends(get_current_user),
):
    """Validate a workflow spec against the DSL schema and tool registry (§13.3)."""
    result = await _validate_spec(body.workflow_spec, session)
    return ValidateWorkflowResponse(**result)


# ---------------------------------------------------------------------------
# Validation logic
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = ["name", "version", "trigger", "steps"]
VALID_STEP_TYPES = {"action", "approval", "condition", "delay", "parallel", "notification", "sub_playbook"}
DURATION_PATTERN_PARTS = {"s", "m", "h", "d"}


async def _validate_spec(spec: dict, session: AsyncSession) -> dict[str, Any]:
    """Validate a workflow spec. Returns {valid, errors, warnings}."""
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Required top-level fields
    for field in REQUIRED_TOP_LEVEL:
        if field not in spec:
            errors.append(f"Missing required field: '{field}'")

    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}

    # 2. Trigger must have incident_types
    trigger = spec.get("trigger", {})
    if not trigger.get("incident_types"):
        errors.append("trigger.incident_types must be a non-empty list")

    # 3. Steps validation
    steps = spec.get("steps", [])
    if not steps:
        warnings.append("Playbook has no steps")

    step_ids = set()
    referenced_step_ids = set()
    tool_names_used: list[str] = []
    steps_before_index: dict[str, int] = {}

    for i, step in enumerate(steps):
        sid = step.get("id")
        if not sid:
            errors.append(f"Step at index {i} is missing 'id'")
            continue

        if sid in step_ids:
            errors.append(f"Duplicate step id: '{sid}'")
        step_ids.add(sid)
        steps_before_index[sid] = i

        if not step.get("name"):
            errors.append(f"Step '{sid}' is missing 'name'")

        stype = step.get("type")
        if stype not in VALID_STEP_TYPES:
            errors.append(f"Step '{sid}' has invalid type: '{stype}'")

        # Action/notification steps must have a tool
        if stype in ("action", "notification"):
            tool = step.get("tool")
            if not tool:
                errors.append(f"Step '{sid}' (type={stype}) is missing 'tool'")
            else:
                tool_names_used.append(tool)

        # Condition steps must have condition + branches
        if stype == "condition":
            if not step.get("condition"):
                errors.append(f"Step '{sid}' (condition) is missing 'condition' expression")
            if not step.get("branches"):
                warnings.append(f"Step '{sid}' (condition) has no 'branches' defined")

        # Delay steps must have duration
        if stype == "delay" and not step.get("duration"):
            errors.append(f"Step '{sid}' (delay) is missing 'duration'")

        # Collect referenced step IDs
        for ref_field in ("on_success", "on_failure"):
            ref = step.get(ref_field)
            if ref and ref != "abort":
                referenced_step_ids.add(ref)

        branches = step.get("branches", {})
        for branch_val in branches.values():
            if branch_val:
                referenced_step_ids.add(branch_val)

    # 4. Check referenced step IDs exist
    for ref_id in referenced_step_ids:
        if ref_id not in step_ids and ref_id != "__end__":
            errors.append(f"Referenced step '{ref_id}' does not exist")

    # 5. Check tools exist in registry
    if tool_names_used:
        for tool_name in set(tool_names_used):
            tool = await store.get_tool_by_name(session, tool_name)
            if tool is None:
                errors.append(f"Tool '{tool_name}' not found in tool registry")
            elif not tool.is_active:
                errors.append(f"Tool '{tool_name}' is deactivated")
            elif tool.side_effect_level in ("write", "destructive"):
                # Check if there's an approval step before this tool
                action_indices = [
                    steps_before_index[s.get("id", "")]
                    for s in steps
                    if s.get("tool") == tool_name and s.get("id") in steps_before_index
                ]
                approval_indices = [
                    steps_before_index[s.get("id", "")]
                    for s in steps
                    if s.get("type") == "approval" and s.get("id") in steps_before_index
                ]
                for ai in action_indices:
                    if not any(ap < ai for ap in approval_indices):
                        warnings.append(
                            f"Step using '{tool_name}' (side_effect_level={tool.side_effect_level}) "
                            f"has no preceding approval step. Consider adding one for production use."
                        )
                        break

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
