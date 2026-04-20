"""Temporal activities for AutoMend playbook execution (§22).

Infrastructure activities connect to Postgres.
Tool activities connect to Kubernetes, Prometheus, Slack, PagerDuty, Jira.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID

import httpx
from temporalio import activity


# ---------------------------------------------------------------------------
# Helper: get a fresh DB session for activities (no DI available)
# ---------------------------------------------------------------------------

async def _get_session():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.config import get_settings
    settings = get_settings()
    engine = create_async_engine(settings.postgres_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# =============================================
# INFRASTRUCTURE ACTIVITIES
# =============================================


@activity.defn
async def load_playbook_activity(playbook_version_id: str) -> dict:
    """Load a playbook version from Postgres."""
    from app.stores import postgres_store as store
    engine, factory = await _get_session()
    try:
        async with factory() as session:
            version = await store.get_version(session, UUID(playbook_version_id))
            if version is None:
                raise ValueError(f"Playbook version {playbook_version_id} not found")
            return {
                "workflow_spec": version.workflow_spec,
                "spec_checksum": version.spec_checksum,
            }
    finally:
        await engine.dispose()


@activity.defn
async def update_incident_status_activity(incident_id: str, new_status: str) -> dict:
    """Update an incident's status in Postgres."""
    from app.stores import postgres_store as store
    engine, factory = await _get_session()
    try:
        async with factory() as session:
            incident = await store.update_incident(session, UUID(incident_id), status=new_status)
            if incident is None:
                raise ValueError(f"Incident {incident_id} not found")
            await session.commit()
            return {"incident_id": incident_id, "status": new_status}
    finally:
        await engine.dispose()


@activity.defn
async def resolve_incident_activity(incident_id: str) -> dict:
    """Resolve an incident in Postgres."""
    from app.stores import postgres_store as store
    engine, factory = await _get_session()
    try:
        async with factory() as session:
            incident = await store.resolve_incident(session, UUID(incident_id))
            if incident is None:
                raise ValueError(f"Incident {incident_id} not found")
            await session.commit()
            return {"incident_id": incident_id, "status": "resolved"}
    finally:
        await engine.dispose()


@activity.defn
async def record_step_result_activity(
    incident_id: str,
    step_id: str,
    success: bool,
    output: dict | None,
    error: str | None,
) -> dict:
    """Record a step execution result as an incident event."""
    from app.stores import postgres_store as store
    engine, factory = await _get_session()
    try:
        async with factory() as session:
            await store.add_event(
                session, UUID(incident_id), "step_completed",
                {"step_id": step_id, "success": success, "output": output, "error": error},
                actor="temporal-worker",
            )
            await session.commit()
            return {"recorded": True, "step_id": step_id}
    finally:
        await engine.dispose()


# =============================================
# KUBERNETES ACTIVITIES
# =============================================


@activity.defn
async def fetch_pod_logs_activity(input: dict) -> dict:
    """Fetch logs from a Kubernetes pod."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        logs = await v1.read_namespaced_pod_log(
            name=input["pod"],
            namespace=input["namespace"],
            container=input.get("container") or None,
            tail_lines=input.get("tail_lines", 200),
            since_seconds=input.get("since_seconds", 600),
        )
        return {"logs": logs, "line_count": len(logs.split("\n"))}
    finally:
        await v1.api_client.close()


@activity.defn
async def restart_workload_activity(input: dict) -> dict:
    """Restart a Kubernetes workload via rollout restart."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    try:
        patch = {
            "spec": {"template": {"metadata": {"annotations": {
                "automend.io/restartedAt": datetime.now(timezone.utc).isoformat()
            }}}}
        }
        wtype = input["workload_type"]
        name = input["workload_name"]
        ns = input["namespace"]
        if wtype == "deployment":
            await apps_v1.patch_namespaced_deployment(name, ns, patch)
        elif wtype == "statefulset":
            await apps_v1.patch_namespaced_stateful_set(name, ns, patch)
        elif wtype == "daemonset":
            await apps_v1.patch_namespaced_daemon_set(name, ns, patch)
        return {"success": True, "message": f"Restarted {wtype}/{name}"}
    finally:
        await apps_v1.api_client.close()


@activity.defn
async def scale_deployment_activity(input: dict) -> dict:
    """Scale a Kubernetes deployment."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    try:
        current = await apps_v1.read_namespaced_deployment(input["deployment_name"], input["namespace"])
        previous = current.spec.replicas
        await apps_v1.patch_namespaced_deployment_scale(
            input["deployment_name"], input["namespace"],
            {"spec": {"replicas": input["replicas"]}},
        )
        return {"success": True, "previous_replicas": previous, "new_replicas": input["replicas"]}
    finally:
        await apps_v1.api_client.close()


@activity.defn
async def rollback_release_activity(input: dict) -> dict:
    """Rollback a Kubernetes deployment to a previous revision."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    try:
        revision = input.get("revision", 0)
        await apps_v1.patch_namespaced_deployment(
            input["deployment_name"], input["namespace"],
            {"metadata": {"annotations": {"deployment.kubernetes.io/revision": str(revision)}}},
        )
        return {"success": True, "rolled_back_to_revision": revision, "message": "Rollback initiated"}
    finally:
        await apps_v1.api_client.close()


@activity.defn
async def describe_pod_activity(input: dict) -> dict:
    """Get full pod description from Kubernetes."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        pod = await v1.read_namespaced_pod(input["pod"], input["namespace"])
        events_resp = await v1.list_namespaced_event(
            input["namespace"], field_selector=f"involvedObject.name={input['pod']}",
        )
        return {
            "status": pod.status.to_dict() if pod.status else {},
            "events": [e.to_dict() for e in (events_resp.items or [])[:20]],
            "conditions": [c.to_dict() for c in (pod.status.conditions or [])],
            "container_statuses": [c.to_dict() for c in (pod.status.container_statuses or [])],
        }
    finally:
        await v1.api_client.close()


@activity.defn
async def get_node_status_activity(input: dict) -> dict:
    """Get node status from Kubernetes."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        node = await v1.read_node(input["node"])
        events_resp = await v1.list_event_for_all_namespaces(
            field_selector=f"involvedObject.name={input['node']}",
        )
        return {
            "conditions": [c.to_dict() for c in (node.status.conditions or [])],
            "allocatable": node.status.allocatable or {},
            "capacity": node.status.capacity or {},
            "events": [e.to_dict() for e in (events_resp.items or [])[:20]],
        }
    finally:
        await v1.api_client.close()


@activity.defn
async def cordon_node_activity(input: dict) -> dict:
    """Cordon a Kubernetes node."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        await v1.patch_node(input["node"], {"spec": {"unschedulable": True}})
        return {"success": True, "message": f"Node {input['node']} cordoned"}
    finally:
        await v1.api_client.close()


@activity.defn
async def drain_node_activity(input: dict) -> dict:
    """Drain a Kubernetes node (cordon + evict pods)."""
    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        await v1.patch_node(input["node"], {"spec": {"unschedulable": True}})
        pods = await v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={input['node']}")
        evicted = []
        for pod in pods.items:
            if pod.metadata.namespace == "kube-system" and input.get("ignore_daemonsets", True):
                continue
            try:
                eviction = client.V1Eviction(
                    metadata=client.V1ObjectMeta(name=pod.metadata.name, namespace=pod.metadata.namespace),
                    delete_options=client.V1DeleteOptions(
                        grace_period_seconds=input.get("grace_period_seconds", 300),
                    ),
                )
                await v1.create_namespaced_pod_eviction(pod.metadata.name, pod.metadata.namespace, eviction)
                evicted.append(f"{pod.metadata.namespace}/{pod.metadata.name}")
            except Exception:
                pass
        return {"success": True, "evicted_pods": evicted, "message": f"Drained node {input['node']}"}
    finally:
        await v1.api_client.close()


@activity.defn
async def run_diagnostic_script_activity(input: dict) -> dict:
    """Execute a pre-approved diagnostic script in a pod container."""
    ALLOWED_SCRIPTS = {
        "nvidia_smi": ["nvidia-smi"],
        "gpu_memory": ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total", "--format=csv"],
        "disk_usage": ["df", "-h"],
        "process_list": ["ps", "aux"],
        "network_check": ["ss", "-tulnp"],
    }

    script_name = input["script_name"]
    if script_name not in ALLOWED_SCRIPTS:
        return {"exit_code": 1, "stdout": "", "stderr": f"Script '{script_name}' not in allowed list"}

    from kubernetes_asyncio import client, config
    try:
        config.load_incluster_config()
    except config.ConfigException:
        await config.load_kube_config()
    v1 = client.CoreV1Api()
    try:
        command = ALLOWED_SCRIPTS[script_name]
        resp = await v1.connect_get_namespaced_pod_exec(
            input["pod"], input["namespace"],
            command=command,
            container=input.get("container", ""),
            stderr=True, stdout=True,
        )
        return {"exit_code": 0, "stdout": resp, "stderr": ""}
    finally:
        await v1.api_client.close()


# =============================================
# OBSERVABILITY ACTIVITIES
# =============================================


@activity.defn
async def query_prometheus_activity(input: dict) -> dict:
    """Execute a PromQL query against Prometheus."""
    from app.config import get_settings
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        params: dict[str, str] = {"query": input["query"]}
        if input.get("time"):
            params["time"] = input["time"]
        response = await client.get(
            f"{settings.prometheus_url}/api/v1/query",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"result_type": data["data"]["resultType"], "result": data["data"]["result"]}


# =============================================
# NOTIFICATION ACTIVITIES
# =============================================


@activity.defn
async def page_oncall_activity(input: dict) -> dict:
    """Page the on-call engineer via PagerDuty."""
    from app.config import get_settings
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.pagerduty_api_url}/incidents",
            headers={
                "Authorization": f"Token token={settings.pagerduty_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "incident": {
                    "type": "incident",
                    "title": input["title"],
                    "body": {"type": "incident_body", "details": input["body"]},
                    "urgency": "high" if input.get("severity") in ("critical", "high") else "low",
                    "service": {
                        "id": input.get("service_id", settings.pagerduty_default_service_id),
                        "type": "service_reference",
                    },
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"success": True, "page_id": data["incident"]["id"]}


@activity.defn
async def slack_notification_activity(input: dict) -> dict:
    """Send a Slack notification.

    Supports two Slack modes:
      1. Incoming webhook (preferred if `AUTOMEND_SLACK_WEBHOOK_URL` is set):
         POST a JSON payload to the webhook URL. No bot token needed; channel
         is baked into the webhook. Simpler, faster to set up.
      2. Bot API (fallback): `chat.postMessage` with a Bearer token. Used
         when only `AUTOMEND_SLACK_BOT_TOKEN` is set.
    """
    from app.config import get_settings
    settings = get_settings()
    color_map = {"red": "#FF0000", "orange": "#FF8C00", "yellow": "#FFD700", "green": "#00FF00"}
    color = color_map.get(input.get("severity_color", "orange"), "#FF8C00")
    message = input["message"]

    async with httpx.AsyncClient() as client:
        if settings.slack_webhook_url:
            # Webhook mode — body shape is Slack's "Incoming Webhook" format.
            response = await client.post(
                settings.slack_webhook_url,
                json={
                    "text": message,
                    "attachments": [{"color": color, "text": message}],
                },
                timeout=30,
            )
            response.raise_for_status()
            # Webhooks return "ok" as plain text body on success.
            return {"success": response.text.strip() == "ok", "mode": "webhook"}

        # Bot API mode
        response = await client.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
            json={
                "channel": input.get("channel") or settings.slack_default_channel,
                "attachments": [{"color": color, "text": message}],
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"success": data.get("ok", False), "ts": data.get("ts"), "mode": "bot"}


@activity.defn
async def slack_approval_activity(input: dict) -> dict:
    """Send a Slack approval request + poll for a decision.

    Creates an `approval_request` row, posts a Slack notification via the
    configured webhook (or bot API), and polls the DB until the request is
    approved/rejected or the timeout elapses.
    """
    from app.config import get_settings
    from app.stores import postgres_store as store
    settings = get_settings()
    engine, factory = await _get_session()
    try:
        async with factory() as session:
            approval = await store.create_approval_request(
                session,
                incident_id=UUID(input["incident_id"]) if input.get("incident_id") else None,
                workflow_id="current",
                step_name="approval",
                requested_action=input["message"],
                requested_by="system",
            )
            await session.commit()
            approval_id = approval.id

        # Best-effort Slack notification that an approval is waiting. Failure
        # here shouldn't abort the workflow — the approval is already recorded
        # in Postgres; an operator can still approve via the UI / DB.
        try:
            async with httpx.AsyncClient() as client:
                slack_text = (
                    f":warning: *Approval required*\n"
                    f"> {input['message']}\n"
                    f"Approval id: `{approval_id}`"
                )
                if settings.slack_webhook_url:
                    await client.post(
                        settings.slack_webhook_url,
                        json={"text": slack_text},
                        timeout=15,
                    )
                elif settings.slack_bot_token:
                    await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={"Authorization": f"Bearer {settings.slack_bot_token}"},
                        json={
                            "channel": input.get("channel") or settings.slack_default_channel,
                            "text": slack_text,
                        },
                        timeout=15,
                    )
        except Exception:
            # Logged but not fatal — approval tracking lives in Postgres.
            import logging
            logging.getLogger(__name__).warning(
                "Failed to post approval notification to Slack", exc_info=True,
            )

        # Poll for decision
        timeout_secs = input.get("timeout_minutes", 30) * 60
        elapsed = 0
        poll_interval = 10
        while elapsed < timeout_secs:
            async with factory() as session:
                result = await store.get_approval_request(session, approval_id)
                if result and result.status in ("approved", "rejected"):
                    if result.status == "approved":
                        return {"approved": True, "approver": result.decided_by, "timestamp": str(result.decided_at)}
                    else:
                        raise RuntimeError(f"Approval rejected by {result.decided_by}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise RuntimeError("Approval timed out")
    finally:
        await engine.dispose()


# =============================================
# TICKETING ACTIVITIES
# =============================================


@activity.defn
async def open_ticket_activity(input: dict) -> dict:
    """Create a ticket in Jira."""
    from app.config import get_settings
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.jira_url}/rest/api/3/issue",
            auth=(settings.jira_email, settings.jira_api_token),
            json={
                "fields": {
                    "project": {"key": settings.jira_project_key},
                    "summary": input["title"],
                    "description": {
                        "type": "doc", "version": 1,
                        "content": [{"type": "paragraph", "content": [{"type": "text", "text": input["description"]}]}],
                    },
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": input.get("priority", "Medium").capitalize()},
                    "labels": input.get("labels", []),
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return {"success": True, "ticket_id": data["key"], "ticket_url": f"{settings.jira_url}/browse/{data['key']}"}
