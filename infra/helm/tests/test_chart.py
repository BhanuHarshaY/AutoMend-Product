"""Structural invariants for the AutoMend Helm chart.

Runs entirely offline — no cluster, no image pulls. Invokes `helm template`
via conftest fixtures, parses the output YAML, and asserts the chart keeps
producing well-formed manifests as it evolves.

Run:
    pytest infra/helm/tests/ -v
"""

from __future__ import annotations

import subprocess

import pytest

from .conftest import CHART_DIR, HELM, _run, by_component, by_kind


# ===================================================================
# LINT — `helm lint` must exit 0
# ===================================================================


class TestHelmLint:
    def test_lint_clean(self):
        result = _run([HELM, "lint", str(CHART_DIR)])
        assert result.returncode == 0, (
            f"helm lint failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        )


# ===================================================================
# TOPOLOGY — expected Deployments and Services exist
# ===================================================================


APP_COMPONENTS = (
    "api",
    "classifier",
    "frontend",
    "window-worker",
    "correlation-worker",
    "temporal-worker",
)
SERVICED_COMPONENTS = ("api", "classifier", "frontend")
DEV_DEP_COMPONENTS = ("postgres", "redis", "temporal")


class TestTopology:
    def test_six_app_deployments(self, rendered_local):
        for c in APP_COMPONENTS:
            assert by_component(rendered_local, "Deployment", c), \
                f"Missing Deployment for component '{c}'"

    def test_three_app_services(self, rendered_local):
        for c in SERVICED_COMPONENTS:
            assert by_component(rendered_local, "Service", c), \
                f"Missing Service for component '{c}'"

    def test_workers_have_no_service(self, rendered_local):
        for c in ("window-worker", "correlation-worker", "temporal-worker"):
            assert by_component(rendered_local, "Service", c) is None, \
                f"Worker '{c}' unexpectedly has a Service"

    def test_dev_deps_present_with_local_values(self, rendered_local):
        for c in DEV_DEP_COMPONENTS:
            assert by_component(rendered_local, "Deployment", c), \
                f"values-local should render {c} Deployment"
            assert by_component(rendered_local, "Service", c), \
                f"values-local should render {c} Service"

    def test_postgres_has_pvc(self, rendered_local):
        assert by_component(rendered_local, "PersistentVolumeClaim", "postgres")

    def test_dev_deps_absent_with_external_values(self, rendered_external):
        for c in DEV_DEP_COMPONENTS:
            assert by_component(rendered_external, "Deployment", c) is None, \
                f"external render should not include dev dep '{c}'"


# ===================================================================
# IMAGES — dev deps use the expected images
# ===================================================================


class TestDevDepImages:
    def test_postgres_uses_pgvector(self, rendered_local):
        dep = by_component(rendered_local, "Deployment", "postgres")
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert "pgvector/pgvector" in image, \
            f"Postgres must use pgvector image (got {image})"

    def test_redis_uses_alpine(self, rendered_local):
        dep = by_component(rendered_local, "Deployment", "redis")
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert "redis" in image and "alpine" in image

    def test_temporal_uses_auto_setup(self, rendered_local):
        dep = by_component(rendered_local, "Deployment", "temporal")
        image = dep["spec"]["template"]["spec"]["containers"][0]["image"]
        assert "temporalio/auto-setup" in image


# ===================================================================
# CONFIGMAP — every AUTOMEND_* env the backend reads is present
# ===================================================================


# Full list of env vars the backend's app.config.Settings surface reads. Split
# into non-secret (ConfigMap) vs secret (Secret). Keep synced with
# backend/app/config.py.
EXPECTED_CONFIGMAP_KEYS = {
    "AUTOMEND_APP_ENV",
    "AUTOMEND_LOG_LEVEL",
    "AUTOMEND_CORS_ORIGINS",
    "AUTOMEND_POSTGRES_HOST",
    "AUTOMEND_POSTGRES_PORT",
    "AUTOMEND_POSTGRES_USER",
    "AUTOMEND_POSTGRES_DB",
    "AUTOMEND_REDIS_HOST",
    "AUTOMEND_REDIS_PORT",
    "AUTOMEND_TEMPORAL_SERVER_URL",
    "AUTOMEND_CLASSIFIER_SERVICE_URL",
    "AUTOMEND_CLASSIFIER_ENDPOINT",
    "AUTOMEND_CLASSIFIER_CONFIDENCE_THRESHOLD",
    "AUTOMEND_ARCHITECT_PROVIDER",
    "AUTOMEND_ARCHITECT_API_BASE_URL",
    "AUTOMEND_ARCHITECT_MODEL",
    "AUTOMEND_ARCHITECT_LOCAL_ENDPOINT",
    "AUTOMEND_EMBEDDING_API_BASE_URL",
    "AUTOMEND_EMBEDDING_MODEL",
    "AUTOMEND_EMBEDDING_DIMENSIONS",
    "AUTOMEND_WINDOW_SIZE_SECONDS",
    "AUTOMEND_MAX_WINDOW_ENTRIES",
    "AUTOMEND_JWT_ALGORITHM",
}

EXPECTED_SECRET_KEYS = {
    "AUTOMEND_JWT_SECRET",
    "AUTOMEND_POSTGRES_PASSWORD",
    "AUTOMEND_REDIS_PASSWORD",
    "AUTOMEND_ARCHITECT_API_KEY",
    "AUTOMEND_EMBEDDING_API_KEY",
    "AUTOMEND_SLACK_BOT_TOKEN",
    "AUTOMEND_PAGERDUTY_API_KEY",
    "AUTOMEND_JIRA_API_TOKEN",
}


class TestConfigSurface:
    def _app_configmap(self, docs: list[dict]) -> dict:
        """Return the main app ConfigMap (the one with AUTOMEND_* keys).

        values-local.yaml also renders a postgres-initdb ConfigMap (for the
        `CREATE DATABASE temporal` init scripts); we only care about the
        app-config one here.
        """
        for cm in by_kind(docs, "ConfigMap"):
            if "AUTOMEND_APP_ENV" in cm.get("data", {}):
                return cm
        raise AssertionError("No ConfigMap with AUTOMEND_* keys found")

    def test_configmap_exists(self, rendered_local):
        assert self._app_configmap(rendered_local) is not None

    def test_configmap_has_all_expected_keys(self, rendered_local):
        cm = self._app_configmap(rendered_local)
        actual_keys = set(cm["data"].keys())
        missing = EXPECTED_CONFIGMAP_KEYS - actual_keys
        assert not missing, f"ConfigMap missing env keys: {sorted(missing)}"

    def test_secret_has_all_expected_keys(self, rendered_local):
        secs = by_kind(rendered_local, "Secret")
        assert len(secs) == 1, f"Expected 1 Secret, got {len(secs)}"
        actual_keys = set(secs[0]["stringData"].keys())
        missing = EXPECTED_SECRET_KEYS - actual_keys
        assert not missing, f"Secret missing env keys: {sorted(missing)}"

    def test_cors_origins_is_json(self, rendered_local):
        import json
        cm = self._app_configmap(rendered_local)
        value = cm["data"]["AUTOMEND_CORS_ORIGINS"]
        parsed = json.loads(value)
        assert isinstance(parsed, list)
        assert all(isinstance(s, str) for s in parsed)

    def test_configmap_hosts_point_at_subchart_services_in_local(self, rendered_local):
        cm = self._app_configmap(rendered_local)
        assert cm["data"]["AUTOMEND_POSTGRES_HOST"] == "automend-postgres"
        assert cm["data"]["AUTOMEND_REDIS_HOST"] == "automend-redis"
        assert cm["data"]["AUTOMEND_TEMPORAL_SERVER_URL"] == "automend-temporal:7233"

    def test_configmap_hosts_point_at_externals_when_subcharts_off(self, rendered_external):
        cm = self._app_configmap(rendered_external)
        assert cm["data"]["AUTOMEND_POSTGRES_HOST"] == "pg.example.com"
        assert cm["data"]["AUTOMEND_REDIS_HOST"] == "redis.example.com"
        assert cm["data"]["AUTOMEND_TEMPORAL_SERVER_URL"] == "temporal.example.com:7233"


# ===================================================================
# INGRESS — frontend + /api both routed
# ===================================================================


class TestIngress:
    def test_single_ingress(self, rendered_local):
        assert len(by_kind(rendered_local, "Ingress")) == 1

    def test_api_and_frontend_routes(self, rendered_local):
        ing = by_kind(rendered_local, "Ingress")[0]
        paths = {
            p["path"]: p["backend"]["service"]["name"]
            for rule in ing["spec"]["rules"]
            for p in rule["http"]["paths"]
        }
        assert paths.get("/api") == "automend-api", \
            f"/api must route to api service, got {paths}"
        assert paths.get("/") == "automend-frontend", \
            f"/ must route to frontend service, got {paths}"

    def test_ingress_api_rule_first(self, rendered_local):
        """GCLB preserves rule order; /api must come before / to avoid
        the frontend shadowing the API."""
        ing = by_kind(rendered_local, "Ingress")[0]
        paths = [p["path"] for rule in ing["spec"]["rules"] for p in rule["http"]["paths"]]
        assert paths.index("/api") < paths.index("/"), \
            f"/api must be listed before / (got {paths})"


# ===================================================================
# SECURITY — no root containers, runAsNonRoot enforced
# ===================================================================


class TestSecurity:
    @pytest.mark.parametrize("component", APP_COMPONENTS)
    def test_pod_runs_as_non_root(self, rendered_local, component):
        dep = by_component(rendered_local, "Deployment", component)
        pod_sec = dep["spec"]["template"]["spec"].get("securityContext", {}) or {}
        assert pod_sec.get("runAsNonRoot") is True, \
            f"{component} pod missing runAsNonRoot: true"

    @pytest.mark.parametrize("component", APP_COMPONENTS)
    def test_container_drops_all_capabilities(self, rendered_local, component):
        dep = by_component(rendered_local, "Deployment", component)
        c = dep["spec"]["template"]["spec"]["containers"][0]
        caps = (c.get("securityContext") or {}).get("capabilities") or {}
        assert caps.get("drop") == ["ALL"], \
            f"{component} container must drop ALL capabilities (got {caps})"

    @pytest.mark.parametrize("component", APP_COMPONENTS)
    def test_no_privilege_escalation(self, rendered_local, component):
        dep = by_component(rendered_local, "Deployment", component)
        c = dep["spec"]["template"]["spec"]["containers"][0]
        assert (c.get("securityContext") or {}).get("allowPrivilegeEscalation") is False


# ===================================================================
# RESOURCES — every app container has requests + limits
# ===================================================================


class TestResources:
    @pytest.mark.parametrize("component", APP_COMPONENTS)
    def test_app_has_requests_and_limits(self, rendered_local, component):
        dep = by_component(rendered_local, "Deployment", component)
        c = dep["spec"]["template"]["spec"]["containers"][0]
        res = c.get("resources", {})
        assert "requests" in res, f"{component} missing resources.requests"
        assert "limits" in res, f"{component} missing resources.limits"
        for key in ("cpu", "memory"):
            assert key in res["requests"], f"{component}.requests missing {key}"
            assert key in res["limits"], f"{component}.limits missing {key}"


# ===================================================================
# HOOKS — migration Job configured correctly
# ===================================================================


class TestMigrationJob:
    def test_job_exists(self, rendered_local):
        jobs = by_kind(rendered_local, "Job")
        assert len(jobs) == 1
        assert "migrations" in jobs[0]["metadata"]["name"]

    def test_post_install_and_upgrade_hook(self, rendered_local):
        job = by_kind(rendered_local, "Job")[0]
        hook = job["metadata"]["annotations"]["helm.sh/hook"]
        assert "post-install" in hook
        assert "post-upgrade" in hook

    def test_delete_policy_keeps_failed_jobs(self, rendered_local):
        job = by_kind(rendered_local, "Job")[0]
        policy = job["metadata"]["annotations"]["helm.sh/hook-delete-policy"]
        assert "before-hook-creation" in policy
        assert "hook-succeeded" in policy

    def test_runs_alembic_and_seed(self, rendered_local):
        job = by_kind(rendered_local, "Job")[0]
        args = job["spec"]["template"]["spec"]["containers"][0]["args"][0]
        assert "alembic upgrade head" in args
        assert "seed_tools.py" in args
        assert "seed_rules.py" in args

    def test_waits_for_postgres(self, rendered_local):
        job = by_kind(rendered_local, "Job")[0]
        init = job["spec"]["template"]["spec"]["initContainers"]
        assert len(init) == 1
        cmd = " ".join(init[0]["command"])
        assert "AUTOMEND_POSTGRES_HOST" in init[0]["command"][-1]


# ===================================================================
# SERVICE ACCOUNT — one exists, workload-identity-ready
# ===================================================================


class TestServiceAccount:
    def test_one_service_account(self, rendered_local):
        assert len(by_kind(rendered_local, "ServiceAccount")) == 1

    @pytest.mark.parametrize("component", APP_COMPONENTS)
    def test_deployments_reference_sa(self, rendered_local, component):
        dep = by_component(rendered_local, "Deployment", component)
        sa = dep["spec"]["template"]["spec"].get("serviceAccountName")
        assert sa, f"{component} missing serviceAccountName"


# ===================================================================
# SECRETS — create=false path emits no Secret
# ===================================================================


class TestSecretModes:
    def test_secrets_create_false_emits_no_secret(self):
        result = _run([
            HELM, "template", "automend", str(CHART_DIR),
            "-f", str(CHART_DIR / "values-local.yaml"),
            "--set", "secrets.create=false",
            "--set", "secrets.existingSecret=external-auth-sec",
        ])
        assert result.returncode == 0, result.stderr
        import yaml
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        assert not by_kind(docs, "Secret"), "secrets.create=false should emit no Secret"

    def test_secrets_create_false_points_deployments_at_external_name(self):
        result = _run([
            HELM, "template", "automend", str(CHART_DIR),
            "-f", str(CHART_DIR / "values-local.yaml"),
            "--set", "secrets.create=false",
            "--set", "secrets.existingSecret=external-auth-sec",
        ])
        assert result.returncode == 0, result.stderr
        import yaml
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        api_dep = by_component(docs, "Deployment", "api")
        env_from = api_dep["spec"]["template"]["spec"]["containers"][0].get("envFrom", [])
        secret_refs = [e["secretRef"]["name"] for e in env_from if "secretRef" in e]
        assert "external-auth-sec" in secret_refs


# ===================================================================
# RBAC — Task 11.8a: ClusterRole / Roles bound to the app SA
# ===================================================================


class TestRBAC:
    """Two toggles in values.yaml drive RBAC rendering:

        rbac.clusterWide      → ClusterRole + ClusterRoleBinding
        rbac.targetNamespaces → one Role + one RoleBinding per namespace

    Default values.yaml leaves both off so production opts in explicitly.
    values-local.yaml turns on both (cluster-wide for the Clusters API,
    per-ns for future narrower-scope rehearsals).
    """

    @staticmethod
    def _render(*extra_args: str) -> list[dict]:
        result = _run([
            HELM, "template", "automend", str(CHART_DIR),
            *extra_args,
        ])
        assert result.returncode == 0, (
            f"helm template failed:\nSTDOUT:{result.stdout}\nSTDERR:{result.stderr}"
        )
        import yaml as _yaml
        return [d for d in _yaml.safe_load_all(result.stdout) if d]

    # ----- default values.yaml — no RBAC resources rendered --------------

    def test_default_values_emits_no_rbac(self):
        docs = self._render(
            "--set", "external.postgres.host=pg.example.com",
            "--set", "external.redis.host=redis.example.com",
            "--set", "external.temporal.serverUrl=temporal.example.com:7233",
        )
        for kind in ("Role", "RoleBinding", "ClusterRole", "ClusterRoleBinding"):
            assert not by_kind(docs, kind), (
                f"default render unexpectedly emitted a {kind}"
            )

    # ----- values-local render — both modes on ---------------------------

    def test_local_render_emits_cluster_role(self, rendered_local):
        crs = by_kind(rendered_local, "ClusterRole")
        assert len(crs) == 1, f"Expected 1 ClusterRole, got {len(crs)}"

    def test_local_render_emits_cluster_role_binding(self, rendered_local):
        crbs = by_kind(rendered_local, "ClusterRoleBinding")
        assert len(crbs) == 1, f"Expected 1 ClusterRoleBinding, got {len(crbs)}"

    def test_cluster_role_has_deployment_verbs(self, rendered_local):
        cr = by_kind(rendered_local, "ClusterRole")[0]
        rules = cr["rules"]
        # Find the apps/deployments rule
        apps_rules = [r for r in rules if "apps" in r.get("apiGroups", [])]
        assert apps_rules, "ClusterRole missing apps API group rule"
        resources = apps_rules[0]["resources"]
        assert "deployments" in resources
        assert "deployments/scale" in resources
        for verb in ("get", "list", "watch", "patch", "update"):
            assert verb in apps_rules[0]["verbs"], f"missing verb {verb}"

    def test_cluster_role_has_namespaces_verbs(self, rendered_local):
        """namespaces is cluster-scoped; only the ClusterRole grants it.
        Task 11.8b's /api/clusters/default/namespaces endpoint requires this."""
        cr = by_kind(rendered_local, "ClusterRole")[0]
        ns_rules = [
            r for r in cr["rules"]
            if "" in r.get("apiGroups", []) and "namespaces" in r.get("resources", [])
        ]
        assert ns_rules, "ClusterRole missing namespaces rule"
        for verb in ("get", "list", "watch"):
            assert verb in ns_rules[0]["verbs"]

    def test_cluster_role_binding_points_at_service_account(self, rendered_local):
        crb = by_kind(rendered_local, "ClusterRoleBinding")[0]
        sa = by_kind(rendered_local, "ServiceAccount")[0]
        subject = crb["subjects"][0]
        assert subject["kind"] == "ServiceAccount"
        assert subject["name"] == sa["metadata"]["name"], (
            f"ClusterRoleBinding subject {subject['name']} != SA {sa['metadata']['name']}"
        )
        # `helm template` defaults release namespace to "default" unless -n is passed
        assert subject["namespace"] == "default"
        assert crb["roleRef"]["kind"] == "ClusterRole"

    # ----- per-namespace Roles (values-local has [ml, default]) ----------

    def test_local_render_emits_per_namespace_roles(self, rendered_local):
        roles = by_kind(rendered_local, "Role")
        ns_set = {r["metadata"]["namespace"] for r in roles}
        assert ns_set == {"ml", "default"}, (
            f"values-local sets targetNamespaces [ml, default]; got {ns_set}"
        )

    def test_local_render_emits_per_namespace_role_bindings(self, rendered_local):
        rbs = by_kind(rendered_local, "RoleBinding")
        ns_set = {r["metadata"]["namespace"] for r in rbs}
        assert ns_set == {"ml", "default"}

    def test_namespace_role_omits_namespaces_resource(self, rendered_local):
        """Namespaces are cluster-scoped; they belong in ClusterRole only.
        Putting them in a Role is a kubectl-apply-time error."""
        for role in by_kind(rendered_local, "Role"):
            for rule in role["rules"]:
                assert "namespaces" not in rule.get("resources", []), (
                    f"Role in {role['metadata']['namespace']} must not reference namespaces"
                )

    def test_namespace_role_binding_points_at_service_account(self, rendered_local):
        sa = by_kind(rendered_local, "ServiceAccount")[0]
        for rb in by_kind(rendered_local, "RoleBinding"):
            subject = rb["subjects"][0]
            assert subject["kind"] == "ServiceAccount"
            assert subject["name"] == sa["metadata"]["name"]
            # Subject namespace is the release namespace (where the SA lives),
            # NOT the target namespace where the Role is bound.
            assert subject["namespace"] == "default"
            assert rb["roleRef"]["kind"] == "Role"

    # ----- targetNamespaces alone (no clusterWide) ----------------------

    def test_only_target_namespaces_emits_no_cluster_role(self):
        docs = self._render(
            "--set", "external.postgres.host=pg.example.com",
            "--set", "external.redis.host=redis.example.com",
            "--set", "external.temporal.serverUrl=temporal.example.com:7233",
            "--set", "rbac.targetNamespaces={ml}",
        )
        assert not by_kind(docs, "ClusterRole")
        assert not by_kind(docs, "ClusterRoleBinding")
        roles = by_kind(docs, "Role")
        assert len(roles) == 1
        assert roles[0]["metadata"]["namespace"] == "ml"


# ===================================================================
# TEST HOOK POD — the `helm test` pod renders correctly
# ===================================================================


class TestHelmTestHook:
    """Validate templates/tests/test-health.yaml directly.

    Helm filters resources under templates/tests/ from `helm template` output
    (they only render during `helm test` against a live release), so we read
    the file directly and check its structure. The lint pass earlier catches
    any Helm-syntax errors in the file.
    """

    @staticmethod
    def _test_health_source() -> str:
        path = CHART_DIR / "templates" / "tests" / "test-health.yaml"
        assert path.exists(), f"{path} missing"
        return path.read_text(encoding="utf-8")

    def test_file_exists_and_not_empty(self):
        assert len(self._test_health_source().strip()) > 0

    def test_has_test_hook_annotation(self):
        src = self._test_health_source()
        assert '"helm.sh/hook": test' in src or "helm.sh/hook: test" in src, \
            "test-health Pod must carry the helm.sh/hook: test annotation"

    def test_hits_three_services(self):
        """Each of api / classifier / frontend must be referenced so the
        smoke pod actually checks them."""
        src = self._test_health_source()
        # Templated via componentFullname — look for the unquoted service refs.
        for component in ("api", "classifier", "frontend"):
            tag = f'"component" "{component}"'
            assert tag in src, f"test-health pod missing reference to {component}"
