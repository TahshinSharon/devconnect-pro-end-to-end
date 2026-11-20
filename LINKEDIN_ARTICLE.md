# I Built a Full End-to-End DevOps Pipeline From Scratch. Here Is Exactly How It Works.

Last month I decided to stop reading about DevOps and actually build the whole thing. Not a "deploy a static site to S3" tutorial. The real deal -- infrastructure as code, CI/CD pipelines, container orchestration, GitOps, and production-grade observability. All wired together on a single project.

The result is DevConnect Pro -- a microservices application with 10 services, fully automated from a git push all the way to a verified production deployment on AWS.

This article walks through every layer of the system, what I chose, why I chose it, and what I learned building it.

---

## The Problem I Wanted to Solve

Most DevOps learning paths teach tools in isolation. You learn Docker in one course, Kubernetes in another, CI/CD somewhere else. But nobody shows you how all these pieces connect in a real project.

I wanted to build something where:
- A single git push triggers tests, security scans, image builds, and a production deployment
- The deployment is fully automated with no manual SSH or kubectl commands
- If something breaks, I can see exactly where and why through dashboards and logs
- The entire infrastructure is reproducible from code -- no clicking around in the AWS Console

---

## The Architecture

Here is what the full system looks like:

```
Developer pushes code
       |
       v
GitHub Actions Pipeline
  [Tests] -> [Security Scans] -> [Docker Build + Push] -> [GitOps Update]
       |
       v
ArgoCD (running on EC2)
  Watches the Git repo for manifest changes
  Automatically syncs desired state to Kubernetes
       |
       v
k3s Kubernetes Cluster (on EC2)
  Runs all 10 services + PostgreSQL + Redis
       |
       v
Observability Stack
  Prometheus (metrics) + Grafana (dashboards) + Loki (logs) + Alloy (log shipping)
```

Let me break down each layer.

---

## Layer 1: The Application -- 10 Microservices

DevConnect Pro is a social platform built as microservices. Each service is a separate Python FastAPI application with its own Dockerfile and responsibility:

| Service | What It Does | Port |
|---------|-------------|------|
| Auth Service | User registration, login, JWT token generation | 8001 |
| User Service | Profile management, bios | 8002 |
| Post Service | Post creation via async queue, feed retrieval | 8003 |
| Like Service | Like/unlike posts | 8004 |
| Comment Service | Comments with optional image URLs | 8005 |
| Analytics Service | Aggregated metrics -- users, posts, engagement | 8006 |
| Worker Service | Background job processor, consumes Redis queue | 9100 |
| Frontend | React SPA served by Nginx with API reverse proxy | 3000 |
| PostgreSQL | Shared relational database | 5432 |
| Redis | Message queue for async post creation | 6379 |

The post creation flow is intentionally asynchronous. When a user creates a post, the Post Service pushes a job to a Redis queue and returns immediately. The Worker Service picks up the job, writes the post to PostgreSQL, and updates the job status. The frontend shows a brief loading animation so the async flow is visible.

Every FastAPI service exposes a `/prometheus` endpoint with custom application metrics -- request counts, latencies, and service-specific counters. This becomes important later when we wire up monitoring.

**Why microservices for this project?** Not because a social app needs them. Because deploying, monitoring, and debugging 10 services teaches you problems you will never encounter with a monolith -- service discovery, inter-service networking, independent scaling, and cascading failures.

---

## Layer 2: Local Development with Docker Compose

Before anything touches AWS, the entire application runs locally with a single command:

```bash
docker compose up --build -d
```

The `docker-compose.yml` defines all 10 services, their dependencies, health checks, and a shared bridge network. Services reference each other by container name -- `postgres`, `redis`, `auth-service` -- which is the same naming convention used later in Kubernetes.

Health checks are critical here. PostgreSQL has a `pg_isready` check, Redis has `redis-cli ping`, and the application services wait for database readiness using `depends_on` with `condition: service_healthy`. Without these, services start before the database is ready and crash on the first query.

I kept the Docker Compose service names identical to the Kubernetes service names on purpose. This means the Nginx reverse proxy configuration works in both environments without changes -- `/api/auth/` routes to `auth-service:8000` whether you are running locally or in a Kubernetes cluster.

---

## Layer 3: Infrastructure as Code with Terraform

The entire AWS infrastructure is defined in Terraform. No manual resource creation.

What Terraform provisions:
- An EC2 instance running Ubuntu 22.04 (t3.large -- 2 vCPU, 8 GiB RAM)
- A Security Group allowing SSH (22), HTTP (80), and HTTPS (443)
- An SSH key pair uploaded from a local public key
- An IAM role with SSM access for optional Session Manager connectivity
- A 30 GB encrypted gp3 EBS root volume

The Terraform code uses several patterns worth noting:

**Dynamic security group rules.** Admin ports (Grafana on 3001, ArgoCD on 8080, Prometheus on 9090) are behind a boolean toggle. They are closed by default and only opened when `enable_monitoring_admin_ports = true`. In production, you would use SSH tunnels or kubectl port-forward instead.

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

**Conditional key pair creation.** If you already have an AWS key pair, you pass its name. Otherwise, Terraform creates one from your local public key. This avoids the "I already have a key pair" friction that blocks many tutorials.

**User data script.** The EC2 instance boots with a startup script that installs base packages, enables kernel parameters for Kubernetes networking (`bridge-nf-call-iptables`, `ip_forward`), and displays a welcome banner via `/etc/motd`.

After `terraform apply`, you get the SSH command and public IP as outputs. The infrastructure is up in about 3 minutes.

---

## Layer 4: Kubernetes with k3s

I chose k3s over full Kubernetes (kubeadm) or managed services (EKS) for a specific reason -- it installs in one command and runs on a single node. For a learning project, the overhead of a multi-node cluster adds complexity without adding understanding.

k3s gives you everything that matters:
- Full Kubernetes API compatibility
- Built-in Traefik ingress controller
- CoreDNS for service discovery
- Local-path storage provisioner

The Kubernetes manifests are organized using Kustomize:

```
k8s/
  base/                              # Base manifests
    00-namespace.yaml                # devconnect namespace
    10-configmap.yaml                # Non-secret config (DB name, Redis URL, JWT algorithm)
    11-secret.yaml                   # Secrets (DB password, JWT secret)
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

A few design decisions in the Kubernetes manifests:

**ConfigMaps and Secrets are separated.** Non-sensitive values (database name, Redis URL, JWT algorithm) go in a ConfigMap. Sensitive values (database password, JWT signing secret) go in a Secret. Each service pod references both via `envFrom` or individual `valueFrom` entries.

**PostgreSQL and Redis are StatefulSets, not Deployments.** StatefulSets provide stable network identities and persistent volume claims. When a pod restarts, it reconnects to the same storage. Deployments would lose data on restart.

**Every service has readiness and liveness probes.** Readiness probes hit `/health` endpoints -- Kubernetes will not send traffic to a pod that is not ready. Liveness probes restart pods that are stuck. These are not optional. Without them, Kubernetes has no way to know if your application is actually working.

**Resource requests and limits are set for every container.** This prevents one misbehaving service from consuming all the node's memory and crashing everything else. The Worker Service gets lower limits (50m CPU, 128Mi memory) because it is a background processor, while application services get more (100m CPU, 160Mi memory).

---

## Layer 5: CI/CD with GitHub Actions

The pipeline has 6 stages and runs on every push to `main` and every pull request:

### Stage 1: Tests
- Python tests with pytest across all backend services
- Frontend smoke tests with npm test
- Frontend build validation

### Stage 2: Security Scans
- **Trivy filesystem scan** -- scans the entire codebase for known vulnerabilities in dependencies. Fails the pipeline on CRITICAL severity.
- **OWASP Dependency-Check** -- generates an HTML report of dependency vulnerabilities. Fails on CVSS score 9+. The report is uploaded as a build artifact for review.
- **SonarQube** (optional) -- static code analysis for code smells, bugs, and security hotspots. Only runs if `SONAR_TOKEN` and `SONAR_HOST_URL` secrets are configured.

### Stage 3: Docker Build, Scan, and Push
This runs as a matrix strategy across all 8 application images in parallel. For each service:
1. Build the Docker image locally
2. Run Trivy image scan on the built image (fails on CRITICAL or HIGH vulnerabilities)
3. Push to DockerHub with two tags: the git SHA and `latest`

The matrix strategy means all 8 images build simultaneously, cutting the build time significantly. If one image fails its security scan, it fails independently without blocking the others.

### Stage 4: GitOps Manifest Update
A Python script updates `k8s/overlays/prod/kustomization.yaml` with the new image tag (the git SHA). This commit is pushed back to the repo. This is the bridge between CI and CD -- instead of SSHing into the server and running `kubectl apply`, we update a file in Git. ArgoCD handles the rest.

### Stage 5: ArgoCD Runtime Verification
The pipeline SSHes into the EC2 instance and verifies:
- ArgoCD application shows `Synced` and `Healthy`
- All 8 deployments and 2 statefulsets complete their rollout
- Prometheus endpoints on each service return expected metrics
- ServiceMonitors are registered in the monitoring namespace

This is the part most tutorials skip. Building and pushing images is not enough. You need to verify that the deployment actually worked, that pods are running, and that the application is serving traffic.

### Stage 6: Email Notification
A branded HTML email is sent with the pipeline result -- which stages passed, which failed, with a direct link to the workflow run. Uses Gmail SMTP with App Password authentication.

---

## Layer 6: GitOps with ArgoCD

ArgoCD is the core of the deployment model. Here is how it works:

1. ArgoCD runs inside the Kubernetes cluster in its own namespace
2. It is configured with an `Application` custom resource that points to the GitHub repository and the path `k8s/overlays/prod`
3. Every 3 minutes (or on webhook), ArgoCD compares the desired state (Git manifests) with the actual state (what is running in the cluster)
4. If they differ, ArgoCD automatically applies the changes

The key configuration in the ArgoCD Application:

```yaml
syncPolicy:
  automated:
    prune: true      # Delete resources removed from Git
    selfHeal: true   # Revert manual changes made directly to the cluster
```

`prune: true` means if I remove a service from the manifests, ArgoCD deletes it from the cluster. `selfHeal: true` means if someone runs `kubectl edit` and changes something manually, ArgoCD reverts it back to what Git says. The Git repository is the single source of truth.

This is a fundamental shift from traditional deployment. Nobody SSHes into the server. Nobody runs `kubectl apply` manually. If something needs to change, it goes through a Git commit, a PR review, and the CI pipeline.

---

## Layer 7: Observability -- Prometheus, Grafana, Loki

A deployed application without observability is a black box. You have no idea if it is working until a user complains.

### Metrics with Prometheus

Prometheus scrapes metrics from every service every 30 seconds. The scrape targets are defined using ServiceMonitor CRDs:

- All 6 FastAPI services expose metrics at `/prometheus` (request counts, latencies, error rates)
- The Worker Service exposes metrics at `/metrics` (jobs processed, queue depth)
- Node Exporter provides host-level metrics (CPU, memory, disk, network)
- kube-state-metrics provides Kubernetes object metrics (pod status, replica counts, resource usage)
- cAdvisor provides container-level metrics (CPU/memory per container)

Custom application metrics include:
- `devconnect_http_requests_total` -- total HTTP requests by service, method, and status code
- `devconnect_post_create_requests_total` -- post creation attempts
- `devconnect_worker_jobs_processed_total` -- background jobs completed by the worker

### Dashboards with Grafana

Grafana connects to Prometheus as a data source and provides pre-configured dashboards. The Grafana instance is deployed via the kube-prometheus-stack Helm chart with dashboard sidecar enabled, so any ConfigMap with the right label automatically appears as a dashboard.

### Logs with Loki and Alloy

Loki is a log aggregation system (think "Prometheus but for logs"). Grafana Alloy is deployed as an agent that collects logs from all pods and ships them to Loki. In Grafana, you can query logs by namespace, pod, or service, and correlate them with metrics on the same timeline.

This is powerful for debugging. If you see a spike in error rate on a Grafana graph, you can click on the timestamp and jump to the exact logs from that moment.

---

## What I Learned

**Infrastructure as code changes how you think about servers.** Once I could destroy and recreate the entire EC2 setup in 3 minutes with `terraform apply`, I stopped treating servers as precious. Break something? Destroy and rebuild. This is how production teams work.

**GitOps eliminates an entire class of problems.** "It works on my machine but not in production" disappears when the production state is defined in Git. The cluster always matches what Git says. No drift, no mystery changes.

**Security scanning in CI is non-negotiable.** Trivy found vulnerable base image packages I would never have caught manually. OWASP Dependency-Check flagged transitive dependency vulnerabilities buried three layers deep. Running these on every push means vulnerabilities are caught before they reach production.

**Observability is not a nice-to-have.** The first time a pod crashed in a loop, I had Prometheus metrics showing the exact moment it started failing, Grafana graphs showing the memory spike that caused it, and Loki logs showing the exact stack trace. Without these tools, I would have been guessing.

**The hardest part is wiring everything together.** Individual tools are well-documented. But making Prometheus scrape custom FastAPI metrics, making ArgoCD sync Kustomize overlays with image tags from CI, making GitHub Actions SSH into EC2 to verify deployments -- that integration work is where the real learning happens. No tutorial covers it because every project is different.

---

## Tech Stack Summary

| Layer | Tool | Purpose |
|-------|------|---------|
| Application | Python FastAPI, React, Nginx | 7 backend services + frontend |
| Database | PostgreSQL 16, Redis 7 | Persistence + message queue |
| Containerization | Docker, Docker Compose | Build images, local development |
| Infrastructure | Terraform, AWS EC2 | Provision cloud resources from code |
| Orchestration | k3s (Kubernetes) | Run and manage containers in production |
| Ingress | Traefik | Route external traffic to services |
| CI/CD | GitHub Actions | Test, scan, build, push, verify |
| Security | Trivy, OWASP Dependency-Check | Vulnerability scanning in pipeline |
| GitOps | ArgoCD | Automated deployment from Git state |
| Metrics | Prometheus, kube-state-metrics, cAdvisor | Collect and store metrics |
| Dashboards | Grafana | Visualize metrics and logs |
| Logging | Loki, Grafana Alloy | Centralized log aggregation |
| Manifests | Kustomize | Environment-specific Kubernetes configs |

---

## Repository

The complete source code -- application services, Dockerfiles, Kubernetes manifests, Terraform configs, CI/CD pipeline, monitoring configs, and bootstrap scripts -- is available on GitHub:

github.com/TahshinSharon/devconnect-pro-end-to-end

---

If you are learning DevOps or cloud engineering, I would encourage you to build something like this yourself. Reading about Kubernetes and CI/CD gives you vocabulary. Building a full pipeline gives you understanding. They are not the same thing.

Feel free to reach out if you have questions about any part of this setup. Happy to discuss.

---

#DevOps #Kubernetes #CICD #GitOps #AWS #Terraform #Docker #ArgoCD #Prometheus #Grafana #CloudEngineering #InfrastructureAsCode #GitHub #Microservices #SoftwareEngineering
