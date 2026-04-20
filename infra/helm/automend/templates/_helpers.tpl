{{/* vim: set filetype=mustache: */}}

{{/*
====================================================================
Chart name / fullname
====================================================================
*/}}

{{/*
Expand the name of the chart. Trimmed to 63 chars for DNS-1123 compliance.
*/}}
{{- define "automend.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Full release name. Used as the base for every Kubernetes resource name
emitted by the chart. If the release name already contains the chart name,
collapse them to avoid "automend-automend" style duplicates.
*/}}
{{- define "automend.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Per-component fullname: <release-fullname>-<component>.
Used by component Deployments / Services (Task 11.3) — e.g. automend-api,
automend-window-worker.
Call with: (include "automend.componentFullname" (dict "ctx" . "component" "api"))
*/}}
{{- define "automend.componentFullname" -}}
{{- $fullname := include "automend.fullname" .ctx -}}
{{- printf "%s-%s" $fullname .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Chart label — name-version, sanitised to DNS-1123.
*/}}
{{- define "automend.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
====================================================================
Labels
====================================================================
*/}}

{{/*
Common labels applied to every resource in the release.
*/}}
{{- define "automend.labels" -}}
helm.sh/chart: {{ include "automend.chart" . }}
{{ include "automend.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: automend
{{- end -}}

{{/*
Selector labels — the minimal set safe to use in Deployment spec.selector.
MUST stay immutable across releases (Kubernetes rejects selector changes).
*/}}
{{- define "automend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "automend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Component selector labels — selector labels + component name. Used by per-
component Deployments / Services to differentiate e.g. the API pods from
the worker pods.
Call with: (include "automend.componentSelectorLabels" (dict "ctx" . "component" "api"))
*/}}
{{- define "automend.componentSelectorLabels" -}}
{{ include "automend.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Component labels — full labels + component name.
*/}}
{{- define "automend.componentLabels" -}}
{{ include "automend.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
====================================================================
ServiceAccount
====================================================================
*/}}

{{- define "automend.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "automend.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
====================================================================
Image resolution
====================================================================
*/}}

{{/*
Build a fully-qualified image reference for a component.
Precedence for tag: component.image.tag > global.imageTag > Chart.appVersion
Precedence for pullPolicy: component.image.pullPolicy > global.imagePullPolicy > IfNotPresent
Call with: (include "automend.image" (dict "ctx" . "component" .Values.api))
*/}}
{{- define "automend.image" -}}
{{- $registry := .ctx.Values.global.imageRegistry | default "" -}}
{{- $repo := .component.image.repository -}}
{{- $tag := .component.image.tag | default .ctx.Values.global.imageTag | default .ctx.Chart.AppVersion -}}
{{- if $registry -}}
{{- printf "%s/%s:%s" $registry $repo $tag -}}
{{- else -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "automend.imagePullPolicy" -}}
{{- .component.image.pullPolicy | default .ctx.Values.global.imagePullPolicy | default "IfNotPresent" -}}
{{- end -}}

{{/*
====================================================================
Secrets + ConfigMap references
====================================================================
*/}}

{{/*
Name of the Secret holding sensitive env vars. Either the chart-created one
(when secrets.create=true) or an externally managed Secret.
*/}}
{{- define "automend.secretName" -}}
{{- if .Values.secrets.create -}}
{{- printf "%s-secrets" (include "automend.fullname" .) -}}
{{- else -}}
{{- required "secrets.existingSecret is required when secrets.create=false" .Values.secrets.existingSecret -}}
{{- end -}}
{{- end -}}

{{/*
Name of the ConfigMap holding non-secret env vars.
*/}}
{{- define "automend.configMapName" -}}
{{- printf "%s-config" (include "automend.fullname" .) -}}
{{- end -}}

{{/*
====================================================================
Dependency endpoint resolution (postgres / redis / temporal)
====================================================================

Each of these returns the hostname to use inside AUTOMEND_* env vars. If
the corresponding subchart is enabled, the bitnami / temporalio chart's
service name is returned. Otherwise, .Values.external.*.host is returned
(and must be set by the operator).
*/}}

{{- define "automend.postgresHost" -}}
{{- if .Values.postgres.enabled -}}
{{- include "automend.componentFullname" (dict "ctx" . "component" "postgres") -}}
{{- else -}}
{{- required "external.postgres.host is required when postgres.enabled=false" .Values.external.postgres.host -}}
{{- end -}}
{{- end -}}

{{- define "automend.redisHost" -}}
{{- if .Values.redis.enabled -}}
{{- include "automend.componentFullname" (dict "ctx" . "component" "redis") -}}
{{- else -}}
{{- required "external.redis.host is required when redis.enabled=false" .Values.external.redis.host -}}
{{- end -}}
{{- end -}}

{{- define "automend.temporalServerUrl" -}}
{{- if .Values.temporal.enabled -}}
{{- printf "%s:7233" (include "automend.componentFullname" (dict "ctx" . "component" "temporal")) -}}
{{- else -}}
{{- required "external.temporal.serverUrl is required when temporal.enabled=false" .Values.external.temporal.serverUrl -}}
{{- end -}}
{{- end -}}

{{/*
Cluster-internal URLs for the in-chart components, used by components that
call each other (e.g. workers → classifier).
*/}}
{{- define "automend.classifierServiceUrl" -}}
{{- printf "http://%s:%v" (include "automend.componentFullname" (dict "ctx" . "component" "classifier")) .Values.classifier.service.port -}}
{{- end -}}

{{- define "automend.apiServiceUrl" -}}
{{- printf "http://%s:%v" (include "automend.componentFullname" (dict "ctx" . "component" "api")) .Values.api.service.port -}}
{{- end -}}
