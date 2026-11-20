# I Built a Full End-to-End DevOps Pipeline From Scratch. Here Is Exactly How It Works.

DevConnect Pro: a 10-service microservices platform with a fully automated delivery pipeline -- from git push to verified Kubernetes deployment on AWS. No manual intervention at any stage.

This article covers every layer: infrastructure provisioning, container orchestration, CI/CD, GitOps, security scanning, and observability.

---

## The Gap I Set Out to Close

Most DevOps learning paths teach tools in isolation. What is missing is the integration -- how these systems interact, depend on each other, and fail together.

Goals for this build:
- Single commit to main triggers the full cycle: test, scan, build, deploy, verify
- Zero manual intervention -- no SSH, no kubectl, no console clicks
- Full observability through metrics, dashboards, and centralized logs
- All infrastructure reproducible, versioned, and auditable through code

---

## Complete Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Application | Python, FastAPI, React, Nginx | 7 backend microservices + frontend SPA |
| Data | PostgreSQL 16, Redis 7 | Persistence + async message broker |
| Containers | Docker, Docker Compose | Image builds, local dev environment |
| Infrastructure | Terraform, AWS EC2, IAM, SGs | Declarative cloud provisioning |
| Orchestration | k3s, Kustomize | Container scheduling, manifest management |
| Ingress | Traefik | Traffic routing and load balancing |
| CI/CD | GitHub Actions (6-stage) | Test, scan, build, push, deploy, verify |
| Security | Trivy, OWASP Dep-Check, SonarQube | Filesystem, image, and code scanning |
| GitOps | ArgoCD | Git-driven continuous deployment |
| Metrics | Prometheus, kube-state-metrics, cAdvisor, Node Exporter | Multi-layer metrics collection |
| Visualization | Grafana | Dashboards for metrics and logs |
| Logging | Loki, Grafana Alloy | Log aggregation and correlation |
| VCS | Git, GitHub | Source of truth for app and infra code |

---

## System Architecture

The delivery pipeline operates across seven distinct layers, each with clear boundaries and responsibilities:

```
Developer commits to main
       |
       v
GitHub Actions CI Pipeline
  [Unit Tests] -> [Security Analysis] -> [Container Build + Registry Push] -> [GitOps Manifest Update]
       |
       v
ArgoCD (Declarative Continuous Delivery)
  Monitors repository for manifest changes
  Reconciles desired state against cluster state automatically
       |
       v
k3s Kubernetes Cluster (AWS EC2)
  Orchestrates all 10 services + PostgreSQL + Redis
       |
       v
Observability Stack
  Prometheus (metrics collection) + Grafana (visualization) + Loki (log aggregation) + Alloy (log shipping)
```

---

## Layer 1: Application Architecture -- 10 Microservices

DevConnect Pro is a social platform engineered as a distributed system. Each service is a standalone Python FastAPI application with its own container image and single responsibility:

| Service | Responsibility | Port |
|---------|---------------|------|
| Auth Service | Authentication, JWT token issuance and validation | 8001 |
| User Service | Profile management, user metadata | 8002 |
| Post Service | Asynchronous post creation via queue, feed aggregation | 8003 |
| Like Service | Engagement tracking -- like/unlike operations | 8004 |
| Comment Service | Threaded comments with optional media attachments | 8005 |
| Analytics Service | Aggregated platform metrics -- users, posts, engagement rates | 8006 |
| Worker Service | Background job processor consuming from Redis queue | 9100 |
| Frontend | React SPA served through Nginx with API reverse proxy | 3000 |
| PostgreSQL | Relational data persistence | 5432 |
| Redis | Message broker for asynchronous job processing | 6379 |

The post creation workflow is deliberately asynchronous. When a user submits a post, the Post Service enqueues a job to Redis and returns immediately with an acknowledgment. The Worker Service consumes that job, persists the post to PostgreSQL, and updates the job status. The frontend reflects this with a brief processing animation, making the asynchronous architecture visible to the user.

Every FastAPI service exposes a `/prometheus` endpoint with custom application-level metrics -- request counts, response latencies, and service-specific counters. These become critical downstream when wiring the observability layer.

**Why microservices?** Not because a social application demands them. Because operating, monitoring, and troubleshooting 10 independently deployed services forces you to confront real distributed systems challenges -- service discovery, inter-service networking, independent lifecycle management, and cascading failure propagation. These are problems that never surface in a monolithic deployment.

---

## Layer 2: Local Development with Docker Compose

Before anything touches cloud infrastructure, the entire platform runs locally with a single command:

```bash
docker compose up --build -d
```

The `docker-compose.yml` defines all 10 services with explicit dependency ordering, health checks, and a shared bridge network. Services reference each other by container name -- `postgres`, `redis`, `auth-service` -- matching the exact naming convention used in Kubernetes.

Health checks are essential to this setup. PostgreSQL validates readiness with `pg_isready`, Redis confirms availability with `redis-cli ping`, and application services defer startup until their dependencies report healthy via `depends_on` with `condition: service_healthy`. Without these constraints, services initialize before the datastore is accepting connections and crash on the first query.

I intentionally kept Docker Compose service names identical to Kubernetes Service names. This means the Nginx reverse proxy configuration works without modification across both environments -- `/api/auth/` routes to `auth-service:8000` whether running locally or inside a Kubernetes cluster.

---

## Layer 3: Infrastructure as Code with Terraform

The entire AWS footprint is defined declaratively in Terraform. No manually provisioned resources.

What Terraform provisions:
- An EC2 instance running Ubuntu 22.04 (t3.large -- 2 vCPU, 8 GiB RAM)
- A Security Group with least-privilege ingress rules for SSH (22), HTTP (80), and HTTPS (443)
- An SSH key pair uploaded from a local public key
- An IAM role with SSM access for optional Session Manager connectivity
- A 30 GB encrypted gp3 EBS root volume

Several Terraform patterns are worth highlighting:

**Dynamic security group rules.** Administrative ports (Grafana 3001, ArgoCD 8080, Prometheus 9090) are gated behind a boolean toggle. They remain closed by default and only open when `enable_monitoring_admin_ports = true`. In a production environment, you would access these through SSH tunnels or kubectl port-forward rather than direct exposure.

```hcl
dynamic "ingress" {
  for_each = var.enable_monitoring_admin_ports ? toset([3001, 8080, 9090, 9093, 3100]) : []
  content {
    from_port   = ingress.value
    to_port     = ingress.value
    protocol    = "tcp"
    cidr_blocks = [var.allowed_admin_cidr]
  }
}
```

**Conditional key pair creation.** If an existing AWS key pair is available, its name is passed directly. Otherwise, Terraform generates one from a local public key. This eliminates the "I already have a key pair" friction that stalls many infrastructure tutorials.

**User data bootstrap script.** The EC2 instance boots with a startup script that installs base packages, enables kernel parameters required for Kubernetes networking (`bridge-nf-call-iptables`, `ip_forward`), and configures the system for container workloads.

After `terraform apply`, the SSH command and public IP are returned as outputs. The infrastructure is operational within approximately 3 minutes.

---

## Layer 4: Container Orchestration with k3s

I chose k3s over full Kubernetes (kubeadm) or managed services (EKS) for a deliberate reason -- it installs with a single command and operates effectively on a single node. For a project focused on understanding orchestration concepts, the operational overhead of a multi-node cluster adds complexity without proportional learning value.

k3s provides everything that matters for this use case:
- Full Kubernetes API compatibility
- Built-in Traefik ingress controller
- CoreDNS for service discovery
- Local-path storage provisioner

The Kubernetes manifests are organized using Kustomize with clear separation between base definitions and environment-specific overrides:

```
k8s/
  base/                              # Base manifests
    00-namespace.yaml                # devconnect namespace
    10-configmap.yaml                # Non-secret configuration
    11-secret.yaml                   # Sensitive credentials
    20-postgres.yaml                 # StatefulSet with PVC
    21-redis.yaml                    # StatefulSet with PVC
    30-auth-service.yaml             # Deployment + Service
    31-user-service.yaml
    32-post-service.yaml
    33-like-service.yaml
    34-comment-service.yaml
    35-analytics-service.yaml
    36-worker-service.yaml
    40-frontend.yaml
    50-ingress.yaml                  # Traefik ingress rules
    60-servicemonitors.yaml          # Prometheus scrape targets
    kustomization.yaml
  overlays/
    prod/
      kustomization.yaml             # Image tag overrides for production
  monitoring/
    kube-prometheus-stack-values.yaml
    loki-values.yaml
    alloy-values.yaml
    cadvisor-daemonset.yaml
  argocd/
    devconnect-application.yaml      # ArgoCD Application CRD
```

Key design decisions in the Kubernetes manifests:

**Configuration and secrets are strictly separated.** Non-sensitive values (database name, Redis URL, JWT algorithm) reside in a ConfigMap. Sensitive values (database password, JWT signing secret) reside in a Secret. Each service pod references both via `envFrom` or individual `valueFrom` entries.

**PostgreSQL and Redis are StatefulSets, not Deployments.** StatefulSets provide stable network identities and persistent volume claims. When a pod restarts, it reconnects to the same underlying storage. A Deployment would lose all data on restart.

**Every service defines readiness and liveness probes.** Readiness probes target `/health` endpoints -- Kubernetes does not route traffic to pods that have not passed this check. Liveness probes restart pods that become unresponsive. Without these, Kubernetes has no mechanism to determine whether your application is actually functional.

**Resource requests and limits are explicitly defined for every container.** This prevents any single misbehaving service from consuming all node resources and destabilizing the entire cluster. The Worker Service receives lower allocations (50m CPU, 128Mi memory) as a background processor, while user-facing application services receive proportionally more (100m CPU, 160Mi memory).

---

## Layer 5: Continuous Integration and Delivery with GitHub Actions

The CI/CD pipeline comprises 6 sequential stages and executes on every push to `main` and every pull request:

### Stage 1: Automated Testing
- Backend test suite execution with pytest across all Python services
- Frontend smoke tests with npm test
- Frontend production build validation

### Stage 2: Security Analysis
- **Trivy filesystem scan** -- scans the entire codebase for known vulnerabilities in dependencies. The pipeline fails on any CRITICAL severity finding.
- **OWASP Dependency-Check** -- generates a comprehensive HTML vulnerability report across all project dependencies. The pipeline fails on any finding with a CVSS score of 9 or above. The report is preserved as a build artifact for audit review.
- **SonarQube** (optional) -- static code analysis for code quality issues, bugs, and security hotspots. Executes only when `SONAR_TOKEN` and `SONAR_HOST_URL` secrets are configured.

### Stage 3: Container Build, Security Scan, and Registry Push
Runs as a matrix strategy across all 8 application images in parallel. For each service:
1. Build the Docker image locally using Buildx
2. Execute Trivy image scan against the built image (fails on CRITICAL or HIGH vulnerabilities)
3. Push to DockerHub with two tags: the git SHA (immutable) and `latest`

The matrix strategy means all 8 images build concurrently, reducing total build time significantly. If one image fails its security scan, it fails independently without blocking the others.

### Stage 4: GitOps Manifest Update
A Python script updates `k8s/overlays/prod/kustomization.yaml` with the new image tag (the git SHA). This commit is pushed back to the repository. This is the bridge between CI and CD -- instead of SSHing into a server and running `kubectl apply`, we update a versioned file in Git. ArgoCD handles the rest.

### Stage 5: Runtime Verification
The pipeline establishes an SSH connection to the EC2 instance and verifies the deployment end-to-end:
- ArgoCD application reports `Synced` and `Healthy` status
- All 8 Deployments and 2 StatefulSets complete their rollout successfully
- Prometheus metric endpoints on each service return expected application-level metrics
- ServiceMonitors are registered and operational in the monitoring namespace

This is the stage most tutorials omit entirely. Building and pushing container images is insufficient. You need automated verification that the deployment succeeded, that pods are running, and that the application is serving traffic with the correct behavior.

### Stage 6: Automated Email Notification

The final stage dispatches a branded HTML email notification on every pipeline run -- regardless of whether it succeeded or failed. This runs with `if: always()` so it executes even when upstream stages fail.

**How it works:**

1. A result-decision step evaluates the outcome of all five preceding stages. If every required stage passed (with optional stages like Docker and GitOps allowed to be skipped on PRs), the pipeline status is `SUCCESS`. Otherwise, `FAILED`.

2. The notification job uses `dawidd6/action-send-mail@v3` with Gmail SMTP and App Password authentication. Four secrets drive this: `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, and `SMTP_PASSWORD`. A fifth secret, `CI_EMAIL_RECIPIENT`, controls the destination address.

3. The email body is a fully styled HTML template rendered inline in the workflow YAML -- dark-themed, with a gradient header, a color-coded status badge (green for success, red for failure), a metadata table (repository, branch, commit SHA, actor), a grid showing each stage's individual result, and a direct link to the GitHub Actions workflow run.

4. A plaintext fallback body is included for email clients that do not render HTML.

**Why this matters:** Most CI/CD tutorials stop at "deploy succeeded" in the terminal. In practice, pipeline results need to reach people who are not watching the GitHub Actions tab. Automated email notification closes that feedback loop -- the team knows within seconds whether a push to main landed cleanly or broke something, without anyone checking manually.

---

## Layer 6: Declarative Deployment with ArgoCD

ArgoCD is the cornerstone of the deployment model. Here is how it operates:

1. ArgoCD runs inside the Kubernetes cluster in its own dedicated namespace
2. It is configured with an `Application` custom resource that points to the GitHub repository at path `k8s/overlays/prod`
3. On a continuous polling interval (or via webhook), ArgoCD compares the desired state defined in Git against the actual state running in the cluster
4. If any divergence is detected, ArgoCD automatically reconciles the cluster to match Git

The critical configuration in the ArgoCD Application:

```yaml
syncPolicy:
  automated:
    prune: true      # Remove resources deleted from Git
    selfHeal: true   # Revert any manual cluster modifications
```

`prune: true` ensures that if a service is removed from the manifests, ArgoCD deletes it from the cluster. `selfHeal: true` ensures that if someone runs `kubectl edit` and modifies something directly, ArgoCD reverts it back to the Git-defined state. The Git repository is the single source of truth -- always.

This represents a fundamental shift from imperative deployment. No one SSHes into the server. No one runs `kubectl apply` manually. Every change flows through a Git commit, a pull request review, and the CI pipeline. The cluster state is always auditable, always reproducible, and always versioned.

---

## Layer 7: Observability -- Prometheus, Grafana, Loki

A deployed application without observability is a liability. You have no way to know whether it is functioning correctly until a user reports otherwise.

### Metrics Collection with Prometheus

Prometheus scrapes metrics from every service on a 30-second interval. Scrape targets are defined using ServiceMonitor CRDs:

- All 6 FastAPI services expose metrics at `/prometheus` (request counts, response latencies, error rates)
- The Worker Service exposes metrics at `/metrics` (jobs processed, queue depth, processing duration)
- Node Exporter provides host-level metrics (CPU utilization, memory consumption, disk I/O, network throughput)
- kube-state-metrics provides Kubernetes object metrics (pod status, replica counts, resource utilization)
- cAdvisor provides container-level metrics (per-container CPU and memory consumption)

Custom application metrics include:
- `devconnect_http_requests_total` -- total HTTP requests segmented by service, method, and status code
- `devconnect_post_create_requests_total` -- post creation attempts across the platform
- `devconnect_worker_jobs_processed_total` -- background jobs completed by the worker

### Visualization with Grafana

Grafana connects to Prometheus as a data source and provides pre-configured dashboards. The Grafana instance is deployed via the kube-prometheus-stack Helm chart with the dashboard sidecar enabled, so any ConfigMap carrying the appropriate label is automatically registered as a dashboard.

### Centralized Logging with Loki and Alloy

Loki serves as the log aggregation backend -- the equivalent of Prometheus, but for log data. Grafana Alloy is deployed as a DaemonSet agent that collects logs from all pods across the cluster and ships them to Loki. Within Grafana, logs are queryable by namespace, pod, or service label, and can be correlated directly with metrics on the same timeline.

This correlation is where observability becomes genuinely powerful. When a spike in error rate appears on a Grafana dashboard, you can click on that exact timestamp and immediately surface the corresponding log entries from that moment.

---

## Challenges I Faced Along the Way

Building this was not a smooth, linear process. Every layer introduced its own class of problems. Here are the ones that consumed the most debugging time.

**SSH private key handling in GitHub Actions.** This one cost me multiple commits. GitHub Actions stores secrets as strings, and multi-line secrets like SSH private keys are notoriously fragile. The key kept failing silently during the CD verification stage -- the SSH connection would just refuse to authenticate. The root cause was inconsistent line endings and trailing whitespace being injected when the secret was written to a file. The fix was switching from `echo` to `printf '%s\n'` and being extremely precise about file permissions (600 for the key, 700 for the ~/.ssh directory). It took three consecutive fix commits before the pipeline SSH'd into EC2 reliably.

**CVE threshold tuning with OWASP Dependency-Check.** My initial configuration failed the pipeline on any CVE finding at all, which turned out to be impractical -- transitive dependencies in the Python ecosystem carry a long tail of low-severity findings that are either unfixable or irrelevant to the application context. I landed on `--failOnCVSS 9` as the threshold: fail the pipeline on critical vulnerabilities (CVSS 9.0+) while still generating the full HTML report as a build artifact for manual review of lower-severity findings.

**Vulnerable dependency in a core library.** Trivy flagged PyJWT with CVE-2026-102268 -- a vulnerability in the JWT library that every authentication flow in the platform depended on. This was not a transitive dependency I could ignore. The fix required bumping PyJWT from 2.13.0 to 2.14.0 across all services, verifying that the JWT token issuance and validation behavior remained identical, and confirming the scan passed clean afterward. It was a concrete reminder that security scanning is only useful if you actually act on the findings.

**Dockerfile build failures in CI.** The Docker images that built cleanly on my local machine failed in the GitHub Actions runner. The issue came down to differences in the build context and dependency caching behavior between local Docker and the CI environment using Buildx. Each service needed its Dockerfile reviewed to ensure all required files were properly included in the build context and that the layer caching strategy worked correctly in a stateless CI runner.

**ArgoCD sync timing during verification.** The CD verification stage SSHes into EC2 and checks whether ArgoCD has synced and all pods are healthy. The problem: ArgoCD operates on a polling interval, so immediately after the GitOps manifest commit, the cluster state has not yet converged. I solved this with a polling loop -- up to 60 attempts, 10 seconds apart, checking both sync status and health status. Without this, the verification stage would intermittently fail on perfectly valid deployments simply because it checked too early.

**Keeping Docker Compose and Kubernetes service names aligned.** This was a design decision that paid off but required discipline. The Nginx reverse proxy routes to services by name (`auth-service:8000`, `post-service:8000`). If Docker Compose used one naming convention and Kubernetes used another, the proxy configuration would need environment-specific overrides. By enforcing identical service names across both environments from the start, the same Nginx configuration works unchanged in local development and production.

---

## What I Learned

**Infrastructure as code fundamentally changes your relationship with servers.** Once I could destroy and recreate the entire EC2 environment in 3 minutes with `terraform apply`, I stopped treating servers as fragile, long-lived assets. Something breaks? Tear it down and rebuild. This is how production-grade teams operate, and experiencing it firsthand makes the concept tangible in a way that reading about it never does.

**GitOps eliminates an entire category of operational failures.** The classic "it works on my machine but not in production" problem disappears when the production state is defined declaratively in version control. The cluster always reflects what Git says. No configuration drift, no undocumented changes, no mystery.

**Automated security scanning in CI is non-negotiable for any serious pipeline.** Trivy identified vulnerable base image packages I would never have caught through manual review. OWASP Dependency-Check flagged transitive dependency vulnerabilities buried three layers deep in the dependency tree. Executing these scans on every push ensures vulnerabilities are caught before they ever reach a running environment.

**Observability is not a post-launch concern -- it is a design requirement.** The first time a pod entered a crash loop, I had Prometheus metrics isolating the exact moment it began failing, Grafana graphs showing the memory spike that triggered the OOM kill, and Loki logs surfacing the full stack trace. Without that stack, I would have been reduced to guesswork.

**The integration layer is where the real engineering happens.** Individual tools are well-documented in isolation. But orchestrating Prometheus to scrape custom FastAPI metrics through ServiceMonitor CRDs, configuring ArgoCD to reconcile Kustomize overlays with image tags injected by CI, and having GitHub Actions SSH into EC2 to verify deployment health -- that integration work is where the real understanding develops. No tutorial covers it comprehensively because every system is different.

---

## Live Demo

You can test the running platform here:

[LIVE_URL_PLACEHOLDER]

This is the actual deployment -- the same infrastructure described in this article, running on AWS with the full pipeline behind it.

---

## Get in Touch

If you have questions about any part of this build or want to discuss the architecture in detail, message me or reach out through my portfolio: https://tahshinsharon.github.io/contact/

If you are building a career in DevOps, cloud engineering, or platform engineering, I would strongly encourage you to construct something like this yourself. Reading about Kubernetes and CI/CD gives you vocabulary. Building a complete delivery pipeline from infrastructure provisioning to production observability gives you understanding. Those are fundamentally different things.

---

#DevOps #Kubernetes #CICD #GitOps #AWS #Terraform #Docker #ArgoCD #Prometheus #Grafana #Loki #CloudEngineering #InfrastructureAsCode #GitHub #Microservices #SoftwareEngineering #PlatformEngineering #SRE #ContainerOrchestration #ContinuousDelivery
