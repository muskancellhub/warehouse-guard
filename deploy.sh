#!/usr/bin/env bash
# Deploy or update the StreetCast app on the team cluster, without Cursor.
#
#   Run on the workshop VM:   bash ~/vast-builders-challenge/tools/warehouse-guard/deploy.sh
#   Run it again after any change to app/ (new ride_*.json, brief_*.json, code): it updates in place.
#
# Mirrors .cursor/skills/deployment/deploy-app-no-registry: app code in a ConfigMap, the team's
# VSS login in a Secret, a public python:3.12-slim pod, and an Ingress at /app on the team host.
set -euo pipefail

APP_NAME="${APP_NAME:-streetcast}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/app" && pwd)"
PORT=8080

shopt -s nullglob
configs=(/config/*.config)
if [[ ${#configs[@]} -ne 1 ]]; then
  echo "Expected exactly one /config/*.config, found ${#configs[@]}. Run this on the workshop VM." >&2
  exit 1
fi
CFG="${configs[0]}"
value() { grep "^$1=" "$CFG" | head -1 | cut -d= -f2-; }
TEAM="$(value USERNAME)"
VSS_URL="$(value INGRESS_URL)"
VSS_PASSWORD="$(value PASSWORD)"
if [[ -z "$TEAM" || -z "$VSS_URL" || -z "$VSS_PASSWORD" ]]; then
  echo "USERNAME, INGRESS_URL or PASSWORD is missing from $CFG." >&2
  exit 1
fi

KUBE=""
for candidate in "/config/${TEAM}-k8s.yaml" /config/kubeconfig /config/*k8s*.yaml; do
  if [[ -f "$candidate" ]]; then KUBE="$candidate"; break; fi
done
if [[ -z "$KUBE" ]]; then
  echo "No kubeconfig found in /config (looked for ${TEAM}-k8s.yaml and kubeconfig)." >&2
  exit 1
fi
export KUBECONFIG="$KUBE"
NS="$TEAM"
APP_HOST="video-lab-team-${TEAM#team-}.cosmos.vastdata.com"

if [[ ! -f "$APP_DIR/main.py" ]]; then
  echo "No main.py in $APP_DIR." >&2
  exit 1
fi
if [[ -n "$(find "$APP_DIR" -mindepth 1 -type d)" ]]; then
  echo "Warning: subfolders in $APP_DIR are skipped by the ConfigMap; keep files flat." >&2
fi
size_kb=$(du -sk "$APP_DIR" | cut -f1)
if (( size_kb > 900 )); then
  echo "app/ is ${size_kb} KB; a ConfigMap holds about 1 MB. Remove large files first." >&2
  exit 1
fi

echo "Team ${TEAM} · namespace ${NS} · host ${APP_HOST}"
kubectl cluster-info >/dev/null

echo "1/4  App code -> ConfigMap ${APP_NAME}-code"
kubectl -n "$NS" create configmap "${APP_NAME}-code" --from-file="$APP_DIR" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "2/4  VSS login -> Secret ${APP_NAME}-vss-creds"
kubectl -n "$NS" create secret generic "${APP_NAME}-vss-creds" \
  --from-literal=VSS_URL="$VSS_URL" \
  --from-literal=VSS_USERNAME="$TEAM" \
  --from-literal=VSS_PASSWORD="$VSS_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

# A hash of the code in the pod template makes every code change roll out a fresh pod.
CODE_HASH="$(cat "$APP_DIR"/* | sha256sum | cut -c1-16)"

echo "3/4  Deployment, Service, Ingress (/app on ${APP_HOST})"
kubectl -n "$NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: ${APP_NAME}
  template:
    metadata:
      labels:
        app: ${APP_NAME}
      annotations:
        streetcast/code-hash: "${CODE_HASH}"
    spec:
      containers:
      - name: app
        image: python:3.12-slim
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: ${PORT}
        env:
        - name: PORT
          value: "${PORT}"
        - name: VSS_URL
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_URL
        - name: VSS_USERNAME
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_USERNAME
        - name: VSS_PASSWORD
          valueFrom:
            secretKeyRef:
              name: ${APP_NAME}-vss-creds
              key: VSS_PASSWORD
        volumeMounts:
        - name: code
          mountPath: /code
        workingDir: /code
        command: ["bash", "-c"]
        args:
        - |
          set -euo pipefail
          if [ -f requirements.txt ]; then
            pip install --no-cache-dir -q -r requirements.txt
          fi
          exec python main.py
        readinessProbe:
          httpGet:
            path: /health
            port: ${PORT}
          initialDelaySeconds: 5
          periodSeconds: 10
      volumes:
      - name: code
        configMap:
          name: ${APP_NAME}-code
---
apiVersion: v1
kind: Service
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
spec:
  selector:
    app: ${APP_NAME}
  ports:
  - name: http
    port: 80
    targetPort: ${PORT}
  type: ClusterIP
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: ${APP_NAME}
  labels:
    app: ${APP_NAME}
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /\$2
spec:
  ingressClassName: nginx
  rules:
  - host: ${APP_HOST}
    http:
      paths:
      - path: /app(/|$)(.*)
        pathType: ImplementationSpecific
        backend:
          service:
            name: ${APP_NAME}
            port:
              number: 80
EOF

echo "     waiting for the pod..."
if ! kubectl -n "$NS" rollout status deploy/"$APP_NAME" --timeout=180s; then
  echo "The pod did not become ready. Last log lines:" >&2
  kubectl -n "$NS" logs -l app="$APP_NAME" --tail=40 >&2 || true
  exit 1
fi

echo "4/4  Checks"
printf "     /app/health -> "; curl -sS -m 10 "http://${APP_HOST}/app/health" || echo "failed"
echo
printf "     /app/rides  -> "; curl -sS -m 10 "http://${APP_HOST}/app/rides" | head -c 160 || echo "failed"
echo
echo
echo "Done. Open https://workshop.thecosmoslabs.com and click App."
