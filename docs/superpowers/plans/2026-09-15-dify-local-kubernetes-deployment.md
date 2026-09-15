# Local Kubernetes Dify Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a recoverable Dify Community Edition instance in the local Kubernetes cluster and prepare the Incident platform for one fixed controlled Dify Workflow.

**Architecture:** Install the pinned community Dify chart as release `incident-dify` in `dify-system`. Use the existing nginx ingress at `dify.localhost`, single replicas and RWO hostpath PVCs suited to this Docker Desktop node. The Incident platform remains the control plane and calls only Dify's fixed Workflow API.

**Tech Stack:** Kubernetes 1.34, Helm 3, ingress-nginx, Dify 1.17.0, PostgreSQL, Redis, Weaviate, FastAPI.

**Spec:** `docs/superpowers/specs/2026-09-15-dify-local-kubernetes-deployment-design.md`

## Global Constraints

- Work directly on `main`; never create a worktree or use a sub-agent.
- Only create or modify resources in `dify-system`; do not alter existing namespaces or Ingress objects.
- Pin Dify chart `0.39.0-rc1` and app image version `1.17.0`; never enable a floating Dify image tag.
- All Dify, model and platform Secrets remain in Kubernetes Secrets or process environments, never in Git, response bodies, logs or incident snapshots.
- Use `ReadWriteOnce` PVCs because this Docker Desktop cluster's `hostpath` StorageClass does not provide RWX.
- Dify cannot access MySQL, Prometheus, Elasticsearch, SkyWalking, Feishu or Kubernetes credentials.

---

### Task 1: Render a safe local Dify release

**Files:**
- Create: `/tmp/incident-dify-values.yaml`
- Create: `/tmp/incident-dify-rendered.yaml`
- Test: Helm server-side dry run against namespace `dify-system`

**Interfaces:**
- Consumes: chart `dify/dify` version `0.39.0-rc1`, ingress class `nginx`, default StorageClass `hostpath`.
- Produces: release `incident-dify` and namespace-local Kubernetes objects only.

- [ ] **Step 1: Add the pinned Helm repository and inspect exact chart metadata**

```bash
helm repo add incident-dify https://borispolonsky.github.io/dify-helm
helm repo update incident-dify
helm show chart incident-dify/dify --version 0.39.0-rc1
```

Expected: chart metadata reports `version: 0.39.0-rc1` and `appVersion: 1.17.0`.

- [ ] **Step 2: Generate runtime-only secrets without printing them**

```bash
kubectl create namespace dify-system --dry-run=client -o yaml | kubectl apply -f -
kubectl -n dify-system create secret generic incident-dify-runtime \
  --from-literal=app-secret-key="$(openssl rand -base64 42)" \
  --from-literal=internal-api-key="$(openssl rand -base64 42)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Expected: one namespace and one Secret exist; commands never echo secret values.

- [ ] **Step 3: Create an untracked local values file with RWO persistence and ingress**

```yaml
global:
  consoleApiDomain: dify.localhost
  consoleWebDomain: dify.localhost
  serviceApiDomain: dify.localhost
  appApiDomain: dify.localhost
  appWebDomain: dify.localhost
  filesDomain: dify.localhost
image:
  proxy:
    tag: "1.31.5"
  ssrfProxy:
    tag: "7.2-26.04_beta"
api:
  replicas: 1
  persistence:
    persistentVolumeClaim:
      storageClass: hostpath
      accessModes: [ReadWriteOnce]
      size: 5Gi
worker:
  replicas: 1
pluginDaemon:
  replicas: 1
  persistence:
    persistentVolumeClaim:
      storageClass: hostpath
      accessModes: [ReadWriteOnce]
      size: 5Gi
postgresql:
  architecture: standalone
  primary:
    persistence:
      storageClass: hostpath
      accessModes: [ReadWriteOnce]
      size: 8Gi
redis:
  architecture: standalone
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: dify.localhost
      paths:
        - path: /
          pathType: Prefix
```

Inject `global.appSecretKey` and `global.internalApiKey` from the namespace Secret without printing them. The temporary values file must be mode `600` and removed after a successful installation.

- [ ] **Step 4: Render and run server-side dry validation**

```bash
helm template incident-dify incident-dify/dify \
  --version 0.39.0-rc1 --namespace dify-system \
  -f /tmp/incident-dify-values.yaml > /tmp/incident-dify-rendered.yaml
kubectl apply --dry-run=server -f /tmp/incident-dify-rendered.yaml
```

Expected: only `dify-system` objects validate, no unknown API kinds, and all PVC access modes are `ReadWriteOnce`.

- [ ] **Step 5: Commit**

Do not commit files from `/tmp`, generated Secret values, Helm cache files or rendered manifests. Record validation evidence in the active deployment spec only after installation succeeds.

### Task 2: Install and verify Dify cluster resources

**Files:**
- Modify: `docs/current-state.md`
- Modify: `specs/active/dify-controlled-diagnosis-agent.md`
- Test: Kubernetes readiness, PVC binding, Ingress HTTP reachability

**Interfaces:**
- Consumes: Task 1 values, release and namespace.
- Produces: Ready Dify console at `http://dify.localhost/install`.

- [ ] **Step 1: Install using Helm atomic rollback**

```bash
helm upgrade --install incident-dify incident-dify/dify \
  --version 0.39.0-rc1 --namespace dify-system \
  --create-namespace -f /tmp/incident-dify-values.yaml \
  --wait --timeout 15m --atomic
```

Expected: Helm succeeds, or atomically removes newly created release resources on failure without modifying other namespaces.

- [ ] **Step 2: Verify workloads, persistent volumes and ingress**

```bash
kubectl -n dify-system get deploy,sts,pods,pvc,svc,ingress
kubectl -n dify-system wait --for=condition=Ready pod --all --timeout=10m
curl --fail --max-time 15 -H 'Host: dify.localhost' http://127.0.0.1/install
```

Expected: all enabled Pods Ready, all requested PVCs Bound, and the console returns an HTTP response through ingress.

- [ ] **Step 3: Check namespace isolation and cleanup temporary values**

```bash
kubectl get ingress -A -o wide
kubectl get pods -A -l app.kubernetes.io/instance=incident-dify
rm -f /tmp/incident-dify-values.yaml /tmp/incident-dify-rendered.yaml
```

Expected: only `dify-system` gained Dify resources; source control remains free of generated configuration and Secret values.

- [ ] **Step 4: Update runtime-state documentation**

Update `docs/current-state.md` and the active Dify specification with the installed chart/app version, namespace, ingress address, the fact that the Dify console is initialized separately, and the remaining requirement to configure a model provider and Workflow API Key.

- [ ] **Step 5: Commit**

```bash
git add docs/current-state.md specs/active/dify-controlled-diagnosis-agent.md
git commit -m "docs: 记录本地 Dify 集群部署"
```

### Task 3: Configure the platform's real-Dify connection boundary

**Files:**
- Modify: `docs/dify-workflow-setup.md`
- Test: platform `/health` Dify capability response and a non-secret configuration smoke test

**Interfaces:**
- Consumes: Dify console URL and application API Key created by the operator.
- Produces: platform environment values `II_DIFY_ENABLED`, `II_DIFY_BASE_URL`, `II_DIFY_API_KEY`, `II_DIFY_WORKFLOW_LABEL`, and `II_DIAGNOSIS_CAPABILITY_SECRET`.

- [ ] **Step 1: Initialize Dify and configure an LLM provider in its console**

Open `http://dify.localhost/install`, create the initial local administrator, then configure the selected model provider in Dify. Enter its provider API Key only in the Dify console.

- [ ] **Step 2: Create the fixed Workflow**

Create `incident-diagnosis.v1` with inputs `diagnosis_run_id`, `capability_token`, `output_contract_version`. Configure only the two platform read endpoints from `docs/dify-workflow-setup.md`; do not add arbitrary HTTP, database, monitoring, shell, write or Kubernetes tools. Publish the app and generate one application API Key.

- [ ] **Step 3: Put Dify application and platform secrets into runtime-only configuration**

```text
II_DIFY_ENABLED=true
II_DIFY_BASE_URL=http://dify.localhost
II_DIFY_API_KEY=<Dify application API Key>
II_DIFY_WORKFLOW_LABEL=incident-diagnosis.v1
II_DIAGNOSIS_CAPABILITY_SECRET=<random runtime secret>
II_DIAGNOSIS_DEMO_ENABLED=false
```

Restart only the Incident backend process and inspect `GET /health`. Expected: `dify.enabled=true`, `dify.configured=true`, and no credential values in the body.

- [ ] **Step 4: Run the manual diagnosis sandbox**

From an Incident with an existing successful or partial EvidenceRun, create one DiagnosisRun. Verify Dify receives only its three fixed inputs. Verify Dify's output becomes `REPORT_READY` only if each reference matches frozen evidence; otherwise it becomes `REVIEW_REQUIRED` or `FAILED` without affecting alert intake, Incident operations or evidence collection.

- [ ] **Step 5: Commit documentation-only changes**

```bash
git add docs/dify-workflow-setup.md docs/current-state.md specs/active/dify-controlled-diagnosis-agent.md
git commit -m "docs: 补充 Dify Workflow 联调记录"
```
