#!/bin/bash
# usage: echo_loadtest.sh LABEL
L=${1:-test}
R=$(kubectl get deploy scalable-echo -n echo-embb -o jsonpath='{.status.readyReplicas}')
for run in 1 2 3; do
  kubectl run lt-$run -n echo-embb --rm -i -q --restart=Never --image=curlimages/curl --image-pull-policy=IfNotPresent -- sh -c '
for i in $(seq 1 20); do (for j in $(seq 1 50); do curl -s -o /dev/null -w "%{http_code} %{time_total}\n" http://echo-svc; done) & done; wait' 2>/dev/null \
  | awk -v r=$R -v l=$L -v run=$run '$1==200{s+=$2; n++; a[n]=$2} $1!=200{f++} END{asort(a); printf "%s replicas=%s run=%d ok=%d fail=%d avg=%.1fms p95=%.1fms max=%.1fms\n", l, r, run, n, f+0, s/n*1000, a[int(n*0.95)]*1000, a[n]*1000}'
  sleep 10
done
