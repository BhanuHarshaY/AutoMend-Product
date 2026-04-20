from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # === Application ===
    app_name: str = "automend"
    app_env: str = "development"  # development, staging, production
    debug: bool = False
    log_level: str = "INFO"

    # === API ===
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # === Postgres ===
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "automend"
    postgres_password: str = "automend"
    postgres_db: str = "automend"

    @property
    def postgres_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # === Redis ===
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # === Temporal ===
    temporal_server_url: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "automend-playbook-queue"

    # === Classifier (Model 1) ===
    classifier_service_url: str = "http://localhost:8001"
    classifier_timeout_seconds: int = 30
    classifier_confidence_threshold: float = 0.7
    # Endpoint path on the classifier service. Defaults to "/classify" for the
    # stub; set to "/predict_anomaly" when pointing at the RoBERTa service
    # (inference_backend/ClassifierModel).
    classifier_endpoint: str = "/classify"

    # === Architect (Model 2) ===
    # "anthropic" — hits Anthropic's /v1/messages endpoint with x-api-key auth.
    # "local"     — hits the Qwen vLLM proxy from inference_backend/GeneratorModel
    #               at {architect_api_base_url}{architect_local_endpoint} with
    #               {system_prompt, user_message, max_tokens, temperature}.
    architect_provider: str = "anthropic"
    architect_api_key: str = ""
    architect_api_base_url: str = "https://api.anthropic.com"
    architect_model: str = "claude-sonnet-4-20250514"
    architect_local_endpoint: str = "/generate_workflow"

    # === Embedding ===
    embedding_api_key: str = ""
    embedding_api_base_url: str = "https://api.openai.com/v1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # === Prometheus ===
    prometheus_url: str = "http://localhost:9090"

    # === Slack ===
    # Incoming-webhook mode (preferred when set): POSTs JSON payloads to a
    # hardcoded channel baked into the webhook URL. No bot token / channel
    # required. Get one from https://api.slack.com/messaging/webhooks.
    slack_webhook_url: str = ""
    # Classic Bot API mode (fallback): requires a bot token + per-message
    # channel. Activities prefer webhook_url if both are set.
    slack_bot_token: str = ""
    slack_default_channel: str = "#incident-ops"

    # === PagerDuty ===
    pagerduty_api_url: str = "https://api.pagerduty.com"
    pagerduty_api_key: str = ""
    pagerduty_default_service_id: str = ""

    # === Jira ===
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "OPS"

    # === Auth ===
    jwt_secret: str = "change-me-in-production"
    jwt_expiry_minutes: int = 60
    jwt_refresh_expiry_days: int = 7

    # === Window Worker ===
    window_size_seconds: int = 300  # 5 minutes
    max_window_entries: int = 500
    window_check_interval_seconds: int = 30

    # === Correlation Worker ===
    dedup_cooldown_seconds: int = 900  # 15 minutes
    incident_cooldown_seconds: int = 900

    # === Worker Identity ===
    worker_id: str = "worker-0"

    model_config = {
        "env_prefix": "AUTOMEND_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@lru_cache()
def get_settings() -> Settings:
    return Settings()
