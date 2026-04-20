"""Default seed data for tools and alert rules.

Kept separate from the seed scripts so tests can validate the data
without needing a database connection.
"""

from __future__ import annotations

DEFAULT_TOOLS: list[dict] = [
    {
        "name": "fetch_pod_logs",
        "display_name": "Fetch Pod Logs",
        "description": "Retrieves recent logs from a specified Kubernetes pod. Returns the last N lines or logs within a time range.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
                "container": {"type": "string", "default": ""},
                "tail_lines": {"type": "integer", "default": 200},
                "since_seconds": {"type": "integer", "default": 600},
            },
            "required": ["namespace", "pod"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "logs": {"type": "string"},
                "line_count": {"type": "integer"},
            },
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "query_prometheus",
        "display_name": "Query Prometheus",
        "description": "Executes a PromQL query against Prometheus and returns the result. Useful for checking current metric values, recent trends, and alert context.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query expression"},
                "time": {"type": "string", "description": "Optional evaluation timestamp (ISO 8601)"},
                "range_start": {"type": "string"},
                "range_end": {"type": "string"},
                "step": {"type": "string", "default": "60s"},
            },
            "required": ["query"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "result_type": {"type": "string"},
                "result": {"type": "array"},
            },
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "restart_workload",
        "display_name": "Restart Workload",
        "description": "Performs a rolling restart of a Kubernetes workload (Deployment, StatefulSet, or DaemonSet) by patching the pod template annotation.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "workload_type": {"type": "string", "enum": ["deployment", "statefulset", "daemonset"]},
                "workload_name": {"type": "string"},
            },
            "required": ["namespace", "workload_type", "workload_name"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "scale_deployment",
        "display_name": "Scale Deployment",
        "description": "Scales a Kubernetes Deployment to a specified replica count.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment_name": {"type": "string"},
                "replicas": {"type": "integer", "minimum": 0},
            },
            "required": ["namespace", "deployment_name", "replicas"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "previous_replicas": {"type": "integer"},
                "new_replicas": {"type": "integer"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "rollback_release",
        "display_name": "Rollback Release",
        "description": "Rolls back a Kubernetes Deployment to the previous revision or a specified revision number.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "deployment_name": {"type": "string"},
                "revision": {"type": "integer", "description": "Specific revision to roll back to. 0 = previous."},
            },
            "required": ["namespace", "deployment_name"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "rolled_back_to_revision": {"type": "integer"},
                "message": {"type": "string"},
            },
        },
        "side_effect_level": "destructive",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"],
    },
    {
        "name": "page_oncall",
        "display_name": "Page On-Call Engineer",
        "description": "Sends a page/alert to the on-call engineer via PagerDuty or configured paging system.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "PagerDuty service ID or team identifier"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "incident_url": {"type": "string"},
            },
            "required": ["severity", "title", "body"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "page_id": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "slack_notification",
        "display_name": "Send Slack Notification",
        "description": "Sends a message to a Slack channel. Does not wait for response.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "severity_color": {"type": "string", "enum": ["red", "orange", "yellow", "green"]},
            },
            "required": ["channel", "message"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "ts": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "slack_approval",
        "display_name": "Request Slack Approval",
        "description": "Sends an approval request to a Slack channel and waits for an approved/rejected reaction or button click within a timeout.",
        "category": "notification",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
                "timeout_minutes": {"type": "integer", "default": 30},
                "required_approvers": {"type": "integer", "default": 1},
            },
            "required": ["channel", "message"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "approved": {"type": "boolean"},
                "approver": {"type": "string"},
                "timestamp": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "open_ticket",
        "display_name": "Open Ticket",
        "description": "Creates a ticket in the configured ticketing system (Jira, ServiceNow, PagerDuty, etc.).",
        "category": "ticketing",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "team": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "description", "priority"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "ticket_id": {"type": "string"},
                "ticket_url": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "describe_pod",
        "display_name": "Describe Pod",
        "description": "Returns the full Kubernetes pod description including status, events, conditions, and container states.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
            },
            "required": ["namespace", "pod"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "object"},
                "events": {"type": "array"},
                "conditions": {"type": "array"},
                "container_statuses": {"type": "array"},
            },
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "get_node_status",
        "display_name": "Get Node Status",
        "description": "Returns the status, conditions, allocatable resources, and recent events for a Kubernetes node.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
            },
            "required": ["node"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "conditions": {"type": "array"},
                "allocatable": {"type": "object"},
                "capacity": {"type": "object"},
                "events": {"type": "array"},
            },
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
    {
        "name": "cordon_node",
        "display_name": "Cordon Node",
        "description": "Marks a Kubernetes node as unschedulable (cordon). Existing pods continue running but no new pods will be scheduled.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
            },
            "required": ["node"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "message": {"type": "string"},
            },
        },
        "side_effect_level": "write",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"],
    },
    {
        "name": "drain_node",
        "display_name": "Drain Node",
        "description": "Safely evicts all pods from a Kubernetes node and marks it as unschedulable. Use with caution.",
        "category": "kubernetes",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "grace_period_seconds": {"type": "integer", "default": 300},
                "ignore_daemonsets": {"type": "boolean", "default": True},
                "delete_emptydir_data": {"type": "boolean", "default": False},
            },
            "required": ["node"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "evicted_pods": {"type": "array"},
                "message": {"type": "string"},
            },
        },
        "side_effect_level": "destructive",
        "required_approvals": 1,
        "environments_allowed": ["production", "staging"],
    },
    {
        "name": "run_diagnostic_script",
        "display_name": "Run Diagnostic Script",
        "description": "Executes a pre-approved diagnostic script or command in a pod's container. Only pre-registered scripts are allowed.",
        "category": "observability",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "pod": {"type": "string"},
                "container": {"type": "string"},
                "script_name": {"type": "string", "description": "Name of pre-registered diagnostic script"},
                "args": {"type": "object"},
            },
            "required": ["namespace", "pod", "script_name"],
        },
        "output_schema": {
            "type": "object",
            "properties": {
                "exit_code": {"type": "integer"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
            },
        },
        "side_effect_level": "read",
        "required_approvals": 0,
        "environments_allowed": ["production", "staging", "development"],
    },
]

DEFAULT_ALERT_RULES: list[dict] = [
    {
        "name": "GPU High Memory Pressure",
        "description": "GPU memory usage above 95% for 5 minutes",
        "rule_type": "prometheus",
        "rule_definition": {
            "expr": "DCGM_FI_DEV_FB_USED / DCGM_FI_DEV_FB_TOTAL > 0.95",
            "for": "5m",
            "incident_type": "incident.gpu_memory_failure",
        },
        "severity": "high",
    },
    {
        "name": "GPU High Temperature",
        "description": "GPU temperature above 90°C for 5 minutes",
        "rule_type": "prometheus",
        "rule_definition": {
            "expr": "DCGM_FI_DEV_GPU_TEMP > 90",
            "for": "5m",
            "incident_type": "incident.gpu_thermal",
        },
        "severity": "high",
    },
    {
        "name": "Pod Crash Looping",
        "description": "Pod restarting frequently (>0.1 restarts/min over 15m)",
        "rule_type": "prometheus",
        "rule_definition": {
            "expr": "rate(kube_pod_container_status_restarts_total[15m]) > 0.1",
            "for": "5m",
            "incident_type": "incident.pod_crash_loop",
        },
        "severity": "medium",
    },
    {
        "name": "High Error Rate",
        "description": "Service HTTP 5xx error rate above 5%",
        "rule_type": "prometheus",
        "rule_definition": {
            "expr": 'sum(rate(http_requests_total{status=~"5.."}[5m])) by (service, namespace) / sum(rate(http_requests_total[5m])) by (service, namespace) > 0.05',
            "for": "5m",
            "incident_type": "incident.high_error_rate",
        },
        "severity": "high",
    },
    {
        "name": "Node Not Ready",
        "description": "Kubernetes node is not in Ready condition",
        "rule_type": "prometheus",
        "rule_definition": {
            "expr": 'kube_node_status_condition{condition="Ready",status="true"} == 0',
            "for": "5m",
            "incident_type": "incident.node_not_ready",
        },
        "severity": "critical",
    },
]
