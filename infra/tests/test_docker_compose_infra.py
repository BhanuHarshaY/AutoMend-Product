"""Tests for infra/docker-compose.infra.yml and supporting config files.

Validates structure, required services, ports, healthchecks, volumes,
dependencies, and that all mounted config files exist on disk.
"""

import os
from pathlib import Path

import pytest
import yaml

INFRA_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = INFRA_DIR / "docker-compose.infra.yml"


@pytest.fixture(scope="module")
def compose():
    """Load and parse the docker-compose.infra.yml file."""
    assert COMPOSE_FILE.exists(), f"Compose file not found: {COMPOSE_FILE}"
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


# ---------------------------------------------------------------------------
# Structural / parse tests
# ---------------------------------------------------------------------------


class TestComposeStructure:
    def test_file_parses_as_valid_yaml(self, compose):
        assert isinstance(compose, dict)

    def test_has_services_key(self, compose):
        assert "services" in compose

    def test_has_volumes_key(self, compose):
        assert "volumes" in compose


# ---------------------------------------------------------------------------
# Required services
# ---------------------------------------------------------------------------

EXPECTED_SERVICES = [
    "postgres",
    "redis",
    "postgres-temporal",
    "temporal",
    "temporal-ui",
    "prometheus",
    "alertmanager",
    "loki",
    "grafana",
]


class TestRequiredServices:
    @pytest.mark.parametrize("name", EXPECTED_SERVICES)
    def test_service_present(self, services, name):
        assert name in services, f"Missing service: {name}"

    def test_no_application_services(self, services):
        """Infra-only compose must NOT contain app services."""
        app_services = {"api", "window-worker", "correlation-worker",
                        "temporal-worker", "classifier"}
        present = app_services & set(services.keys())
        assert not present, f"App services should not be in infra compose: {present}"


# ---------------------------------------------------------------------------
# Port mappings
# ---------------------------------------------------------------------------

EXPECTED_PORTS = {
    "postgres": "5432:5432",
    "redis": "6379:6379",
    "temporal": "7233:7233",
    "temporal-ui": "8080:8080",
    "prometheus": "9090:9090",
    "alertmanager": "9093:9093",
    "loki": "3100:3100",
    "grafana": "3001:3000",
}


class TestPorts:
    @pytest.mark.parametrize("service,port", EXPECTED_PORTS.items())
    def test_port_exposed(self, services, service, port):
        ports = services[service].get("ports", [])
        assert port in ports, (
            f"{service} should expose {port}, got {ports}"
        )


# ---------------------------------------------------------------------------
# Healthchecks
# ---------------------------------------------------------------------------

SERVICES_WITH_HEALTHCHECK = ["postgres", "redis", "postgres-temporal"]


class TestHealthchecks:
    @pytest.mark.parametrize("name", SERVICES_WITH_HEALTHCHECK)
    def test_healthcheck_defined(self, services, name):
        hc = services[name].get("healthcheck")
        assert hc is not None, f"{name} must have a healthcheck"
        assert "test" in hc
        assert "interval" in hc
        assert "retries" in hc


# ---------------------------------------------------------------------------
# Named volumes
# ---------------------------------------------------------------------------

EXPECTED_VOLUMES = [
    "postgres_data",
    "postgres_temporal_data",
    "redis_data",
    "grafana_data",
]


class TestVolumes:
    @pytest.mark.parametrize("vol", EXPECTED_VOLUMES)
    def test_volume_declared(self, compose, vol):
        assert vol in compose["volumes"], f"Missing volume: {vol}"


# ---------------------------------------------------------------------------
# Service dependencies
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_temporal_depends_on_postgres_temporal(self, services):
        deps = services["temporal"].get("depends_on", {})
        assert "postgres-temporal" in deps

    def test_temporal_ui_depends_on_temporal(self, services):
        deps = services["temporal-ui"].get("depends_on", [])
        assert "temporal" in deps


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

EXPECTED_IMAGES = {
    "postgres": "pgvector/pgvector:pg16",
    "redis": "redis:7-alpine",
    "postgres-temporal": "postgres:16",
    "temporal": "temporalio/auto-setup:latest",
    "temporal-ui": "temporalio/ui:latest",
    "prometheus": "prom/prometheus:latest",
    "alertmanager": "prom/alertmanager:latest",
    "loki": "grafana/loki:latest",
    "grafana": "grafana/grafana:latest",
}


class TestImages:
    @pytest.mark.parametrize("service,image", EXPECTED_IMAGES.items())
    def test_correct_image(self, services, service, image):
        assert services[service]["image"] == image


# ---------------------------------------------------------------------------
# Mounted config files exist on disk
# ---------------------------------------------------------------------------


class TestConfigFilesExist:
    def test_prometheus_yml_exists(self):
        assert (INFRA_DIR / "prometheus" / "prometheus.yml").exists()

    def test_alert_rules_yml_exists(self):
        assert (INFRA_DIR / "prometheus" / "alert_rules.yml").exists()

    def test_alertmanager_yml_exists(self):
        assert (INFRA_DIR / "alertmanager" / "alertmanager.yml").exists()


# ---------------------------------------------------------------------------
# Config file content validation
# ---------------------------------------------------------------------------


class TestPrometheusConfig:
    @pytest.fixture(scope="class")
    def config(self):
        with open(INFRA_DIR / "prometheus" / "prometheus.yml") as f:
            return yaml.safe_load(f)

    def test_scrape_interval(self, config):
        assert config["global"]["scrape_interval"] == "15s"

    def test_rule_files_reference(self, config):
        assert "alert_rules.yml" in config["rule_files"]

    def test_alertmanager_target(self, config):
        targets = config["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"]
        assert "alertmanager:9093" in targets


class TestAlertRules:
    @pytest.fixture(scope="class")
    def config(self):
        with open(INFRA_DIR / "prometheus" / "alert_rules.yml") as f:
            return yaml.safe_load(f)

    def test_has_groups(self, config):
        assert "groups" in config
        assert len(config["groups"]) > 0

    def test_gpu_alerts_group(self, config):
        group_names = [g["name"] for g in config["groups"]]
        assert "gpu_alerts" in group_names

    def test_at_least_five_rules(self, config):
        rules = config["groups"][0]["rules"]
        assert len(rules) >= 5


class TestAlertmanagerConfig:
    @pytest.fixture(scope="class")
    def config(self):
        with open(INFRA_DIR / "alertmanager" / "alertmanager.yml") as f:
            return yaml.safe_load(f)

    def test_has_route(self, config):
        assert "route" in config

    def test_has_receivers(self, config):
        assert "receivers" in config
        assert len(config["receivers"]) > 0

    def test_webhook_receiver(self, config):
        receiver = config["receivers"][0]
        assert receiver["name"] == "automend-webhook"
        assert "webhook_configs" in receiver

    def test_group_by_fields(self, config):
        group_by = config["route"]["group_by"]
        assert "incident_type" in group_by
        assert "namespace" in group_by


# ---------------------------------------------------------------------------
# Docker Compose CLI validation (if docker is available)
# ---------------------------------------------------------------------------


class TestDockerComposeValidation:
    def test_compose_config_validates(self):
        """Run `docker compose config` to validate the file if Docker is available."""
        import shutil
        import subprocess

        docker = shutil.which("docker")
        if docker is None:
            pytest.skip("Docker not available on this machine")

        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--quiet"],
            capture_output=True,
            text=True,
            cwd=str(INFRA_DIR),
        )
        assert result.returncode == 0, (
            f"docker compose config failed:\n{result.stderr}"
        )
