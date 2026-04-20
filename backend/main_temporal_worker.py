"""Entrypoint: Temporal worker process.

Registers workflows and activities, then polls the task queue.

Usage:  cd backend && python main_temporal_worker.py
"""

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.config import get_settings
from app.temporal.activities import (
    cordon_node_activity,
    describe_pod_activity,
    drain_node_activity,
    fetch_pod_logs_activity,
    get_node_status_activity,
    load_playbook_activity,
    open_ticket_activity,
    page_oncall_activity,
    query_prometheus_activity,
    record_step_result_activity,
    resolve_incident_activity,
    restart_workload_activity,
    rollback_release_activity,
    run_diagnostic_script_activity,
    scale_deployment_activity,
    slack_approval_activity,
    slack_notification_activity,
    update_incident_status_activity,
)
from app.temporal.workflows import DynamicPlaybookExecutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_server_url)

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[DynamicPlaybookExecutor],
        activities=[
            load_playbook_activity,
            resolve_incident_activity,
            update_incident_status_activity,
            record_step_result_activity,
            fetch_pod_logs_activity,
            query_prometheus_activity,
            restart_workload_activity,
            scale_deployment_activity,
            rollback_release_activity,
            page_oncall_activity,
            slack_notification_activity,
            slack_approval_activity,
            open_ticket_activity,
            describe_pod_activity,
            get_node_status_activity,
            cordon_node_activity,
            drain_node_activity,
            run_diagnostic_script_activity,
        ],
    )
    logging.info("Temporal worker starting (queue=%s)", settings.temporal_task_queue)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
