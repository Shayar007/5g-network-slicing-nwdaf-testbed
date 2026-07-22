#!/bin/bash
SLICE="${1:?Usage: measure_time_to_scale.sh SLICE_NAME (e.g. MMTC)}"
NAMESPACE="oai"
POLL_INTERVAL=1
T_TRIGGER=""

echo "Watching for [$SLICE] THRESHOLD CROSSED in nwdaf-slice-controller logs..."
echo "(Ctrl+C to abort)"

while read -r line; do
  echo "$line"
  if echo "$line" | grep -q "\[$SLICE\] THRESHOLD CROSSED"; then
    T_TRIGGER=$(date +%s.%N)
    LOG_TS=$(echo "$line" | grep -oP '^\[\K[0-9-]+ [0-9:]+')
    echo ""
    echo "=== THRESHOLD CROSSED detected for $SLICE at $LOG_TS (local epoch $T_TRIGGER) ==="
    echo "Polling for new UPF pod to become Ready..."
    break
  fi
done < <(kubectl logs -n "$NAMESPACE" deploy/nwdaf-slice-controller -f --since=1s)

if [ -z "$T_TRIGGER" ]; then
  echo "ERROR: THRESHOLD CROSSED never detected. Exiting."
  exit 1
fi

DEP_NAME="oai-upf-auto-auto01"
echo "Watching deployment: $DEP_NAME"

while true; do
  READY=$(kubectl get deployment "$DEP_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "")
  if [ "$READY" = "1" ]; then
    T_READY=$(date +%s.%N)
    ELAPSED=$(echo "$T_READY - $T_TRIGGER" | bc)
    echo ""
    echo "=== UPF $DEP_NAME is Ready ==="
    echo "Time-to-scale: ${ELAPSED} seconds"
    echo "$SLICE,$ELAPSED" >> /tmp/time_to_scale_results.csv
    break
  fi
  sleep "$POLL_INTERVAL"
done

echo "Result appended to /tmp/time_to_scale_results.csv"
