"""Tests for infra/docker-compose.yml (full dev stack) and Dockerfiles.

Validates structure, required services, ports, build contexts, env vars,
dependencies, and that all referenced configs + Dockerfiles exist.
"""

from pathlib import Path

import pytest
import yaml

INFRA_DIR = Path(__file__).resolve().parent.parent
COMPOSE_FILE = INFRA_DIR / "docker-compose.yml"
DOCKERFILES_DIR = INFRA_DIR / "dockerfiles"


@pytest.fixture(scope="module")
def compose():
    assert COMPOSE_FILE.exists(), f"Compose file not found: {COMPOSE_FILE}"
    with open(COMPOSE_FILE) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def services(compose):
    return compose["services"]


# ---------------------------------------------------------------------------
# Structural
# ---------------------------------------------------------------------------


class TestComposeStructure:
    def test_parses_as_yaml(self, compose):
        assert isinstance(compose, dict)

    def test_has_services_and_volumes(self, compose):
        assert "services" in compose
        assert "volumes" in compose


# ---------------------------------------------------------------------------
# Required services — both infra and application
# ---------------------------------------------------------------------------

INFRA_SERVICES = [
    "postgres", "redis", "postgres-temporal", "temporal", "temporal-ui",
    "prometheus", "alertmanager", "loki", "grafana",
]

APP_SERVICES = [
    "api", "window-worker", "correlation-worker", "temporal-worker", "classifier",
]


class TestRequiredServices:
    @pytest.mark.parametrize("name", INFRA_SERVICES)
    def test_infra_service_present(self, services, name):
        assert name in services, f"Missing infra service: {name}"

    @pytest.mark.parametrize("name", APP_SERVICES)
    def test_app_service_present(self, services, name):
        assert name in services, f"Missing app service: {name}"

    def test_total_service_count(self, services):
        assert len(services) == len(INFRA_SERVICES) + len(APP_SERVICES)


# ---------------------------------------------------------------------------
# Application services — build + env
# ---------------------------------------------------------------------------

EXPECTED_BUILD_CONTEXT = "../backend"


class TestApplicationBuildConfig:
    @pytest.mark.parametrize("name,dockerfile", [
        ("api", "../infra/dockerfiles/Dockerfile.api"),
        ("window-worker", "../infra/dockerfiles/Dockerfile.worker"),
        ("correlation-worker", "../infra/dockerfiles/Dockerfile.worker"),
        ("temporal-worker", "../infra/dockerfiles/Dockerfile.temporal-worker"),
        ("classifier", "../infra/dockerfiles/Dockerfile.worker"),
    ])
    def test_build_context_and_dockerfile(self, services, name, dockerfile):
        build = services[name].get("build")
        assert build is not None, f"{name} must have a build config"
        assert build["context"] == EXPECTED_BUILD_CONTEXT
        assert build["dockerfile"] == dockerfile


# ---------------------------------------------------------------------------
# Environment variables — app services point at internal service names
# ---------------------------------------------------------------------------


class TestApplicationEnvironment:
    def test_api_points_at_internal_services(self, services):
        env = services["api"]["environment"]
        assert env["AUTOMEND_POSTGRES_HOST"] == "postgres"
        assert env["AUTOMEND_REDIS_HOST"] == "redis"
        assert env["AUTOMEND_TEMPORAL_SERVER_URL"] == "temporal:7233"
        assert env["AUTOMEND_CLASSIFIER_SERVICE_URL"] == "http://classifier:8001"

    def test_window_worker_points_at_internal_services(self, services):
        env = services["window-worker"]["environment"]
        assert env["AUTOMEND_REDIS_HOST"] == "redis"
        assert env["AUTOMEND_CLASSIFIER_SERVICE_URL"] == "http://classifier:8001"

    def test_correlation_worker_points_at_internal_services(self, services):
        env = services["correlation-worker"]["environment"]
        assert env["AUTOMEND_POSTGRES_HOST"] == "postgres"
        assert env["AUTOMEND_REDIS_HOST"] == "redis"
        assert env["AUTOMEND_TEMPORAL_SERVER_URL"] == "temporal:7233"


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_api_depends_on_pg_redis_temporal(self, services):
        deps = services["api"]["depends_on"]
        assert "postgres" in deps
        assert "redis" in deps
        assert "temporal" in deps

    def test_api_waits_for_pg_healthy(self, services):
        deps = services["api"]["depends_on"]
        assert deps["postgres"]["condition"] == "service_healthy"
        assert deps["redis"]["condition"] == "service_healthy"

    def test_workers_depend_on_their_infra(self, services):
        assert "redis" in services["window-worker"]["depends_on"]
        assert "redis" in services["correlation-worker"]["depends_on"]
        assert "temporal" in services["correlation-worker"]["depends_on"]
        assert "postgres" in services["correlation-worker"]["depends_on"]
        assert "temporal" in services["temporal-worker"]["depends_on"]


# ---------------------------------------------------------------------------
# Port exposure
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
    "api": "8000:8000",
    "classifier": "8001:8001",
}


class TestPorts:
    @pytest.mark.parametrize("service,port", EXPECTED_PORTS.items())
    def test_port_exposed(self, services, service, port):
        ports = services[service].get("ports", [])
        assert port in ports, f"{service} should expose {port}, got {ports}"


# ---------------------------------------------------------------------------
# Named volumes
# ---------------------------------------------------------------------------


class TestVolumes:
    @pytest.mark.parametrize("vol", [
        "postgres_data", "postgres_temporal_data", "redis_data", "grafana_data",
    ])
    def test_volume_declared(self, compose, vol):
        assert vol in compose["volumes"]


# ---------------------------------------------------------------------------
# Dockerfiles
# ---------------------------------------------------------------------------


class TestDockerfiles:
    @pytest.mark.parametrize("name", ["Dockerfile.api", "Dockerfile.worker", "Dockerfile.temporal-worker"])
    def test_dockerfile_exists(self, name):
        assert (DOCKERFILES_DIR / name).exists(), f"Missing Dockerfile: {name}"

    @pytest.mark.parametrize("name", ["Dockerfile.api", "Dockerfile.worker", "Dockerfile.temporal-worker"])
    def test_dockerfile_uses_python_slim(self, name):
        content = (DOCKERFILES_DIR / name).read_text()
        assert "FROM python:3.11-slim" in content

    @pytest.mark.parametrize("name", ["Dockerfile.api", "Dockerfile.worker", "Dockerfile.temporal-worker"])
    def test_dockerfile_installs_pyproject(self, name):
        content = (DOCKERFILES_DIR / name).read_text()
        assert "COPY pyproject.toml" in content
        assert "pip install" in content

    def test_api_dockerfile_runs_uvicorn(self):
        content = (DOCKERFILES_DIR / "Dockerfile.api").read_text()
        assert "uvicorn" in content
        assert "main_api:app" in content
        assert "EXPOSE 8000" in content

    def test_worker_dockerfile_default_window(self):
        content = (DOCKERFILES_DIR / "Dockerfile.worker").read_text()
        assert "main_window_worker.py" in content

    def test_temporal_worker_dockerfile_runs_temporal(self):
        content = (DOCKERFILES_DIR / "Dockerfile.temporal-worker").read_text()
        assert "main_temporal_worker.py" in content


# ---------------------------------------------------------------------------
# Alertmanager full config file
# ---------------------------------------------------------------------------


class TestAlertmanagerFullConfig:
    def test_file_exists(self):
        assert (INFRA_DIR / "alertmanager" / "alertmanager.full.yml").exists()

    def test_webhook_points_at_api_service(self):
        config_path = INFRA_DIR / "alertmanager" / "alertmanager.full.yml"
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        receiver = cfg["receivers"][0]
        url = receiver["webhook_configs"][0]["url"]
        # In the full compose, services talk by container name — not host.docker.internal
        assert "api:8000" in url
        assert "host.docker.internal" not in url


# ---------------------------------------------------------------------------
# Docker CLI validation (optional)
# ---------------------------------------------------------------------------


class TestDockerComposeValidation:
    def test_compose_config_validates(self):
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
        assert result.returncode == 0, f"docker compose config failed:\n{result.stderr}"
