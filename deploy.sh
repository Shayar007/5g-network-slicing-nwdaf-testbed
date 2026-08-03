#!/bin/bash
# deploy.sh -- the ONLY approved way to push controller.py to the cluster.
# Usage: ./deploy.sh
#
# Verifies the script BEFORE touching the cluster. Refuses to deploy if any
# check fails, preventing a repeat of the empty-configmap incident.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROLLER="$SCRIPT_DIR/scripts/controller.py"
NAMESPACE="oai"
CONFIGMAP="nwdaf-controller-script"
DEPLOYMENT="nwdaf-slice-controller"

echo "=== Verifying $CONTROLLER before deploy ==="

LINE_COUNT=$(wc -l < "$CONTROLLER")
if [ "$LINE_COUNT" -lt 100 ]; then
    echo "REFUSING TO DEPLOY: file has only $LINE_COUNT lines (expected 300+)."
    echo "This looks like a truncated/corrupt file. Aborting."
    exit 1
fi

if ! python3 -m py_compile "$CONTROLLER"; then
    echo "REFUSING TO DEPLOY: syntax check failed."
    exit 1
fi

if ! grep -q "^def main" "$CONTROLLER"; then
    echo "REFUSING TO DEPLOY: no 'def main' found -- file looks incomplete."
    exit 1
fi

if ! grep -q "^async def main\|^def main" "$CONTROLLER"; then
    echo "REFUSING TO DEPLOY: main entrypoint missing."
    exit 1
fi

if ! tail -1 "$CONTROLLER" | grep -q "main()"; then
    echo "REFUSING TO DEPLOY: file doesn't end with a main() call -- looks truncated."
    exit 1
fi

echo "All checks passed ($LINE_COUNT lines, syntax OK, main() present)."
echo ""
read -p "Deploy to cluster now? [y/N] " CONFIRM
if [ "$CONFIRM" != "y" ]; then
    echo "Aborted by user."
    exit 0
fi

kubectl create configmap "$CONFIGMAP" \
  --from-file=controller.py="$CONTROLLER" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/"$DEPLOYMENT" -n "$NAMESPACE"
kubectl rollout status deployment/"$DEPLOYMENT" -n "$NAMESPACE" --timeout=60s

echo "=== Deployed. Recent logs: ==="
sleep 5
kubectl logs -n "$NAMESPACE" deploy/"$DEPLOYMENT" --tail=10
