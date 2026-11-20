# DevConnect Pro End-to-End DevOps Plan

This implementation follows the agreed sequence:

1. Add tests
2. Add CI workflow
3. Add DockerHub image publishing
4. Add Kubernetes manifests
5. Add ArgoCD GitOps deployment
6. Add EC2/k3s bootstrap scripts
7. Add monitoring stack
8. Add CI/CD notifications
9. Update README with full student guide

## Delivery Flow

```text
Pull Request
  -> tests
  -> Trivy FS scan
  -> OWASP Dependency-Check
  -> SonarQube scan

Push to main
  -> tests
  -> security gates
  -> build images
  -> Trivy image scan
  -> push images to DockerHub
  -> update k8s image tags
  -> ArgoCD sync
  -> Kubernetes deployment
  -> monitoring + logging
  -> email notification
```

## DockerHub Images

```text
tahshin27031/devconnect-frontend
tahshin27031/devconnect-auth-service
tahshin27031/devconnect-user-service
tahshin27031/devconnect-post-service
tahshin27031/devconnect-like-service
tahshin27031/devconnect-comment-service
tahshin27031/devconnect-analytics-service
tahshin27031/devconnect-worker-service
```

## Kubernetes Runtime

Local Compose service names are preserved as Kubernetes service names where possible, so the frontend Nginx proxy can route the same way in Docker Compose and Kubernetes.

## Monitoring Runtime

Prometheus, Grafana, Loki, Grafana Alloy, node-exporter, kube-state-metrics, and cAdvisor run as platform services in the `monitoring` namespace.
