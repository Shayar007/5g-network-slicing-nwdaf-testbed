#!/bin/bash
Q() { kubectl exec -n oai deploy/oai-mysql -- sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" oai_db -N -e "$0" 2>/dev/null' "$1" 2>/dev/null | tr -d '\r'; }
echo "== node"; kubectl get nodes --no-headers
echo "== IP"; ip -4 -br addr show enp0s31f6
kubectl wait --for=condition=ready pod --all -n oai --timeout=240s > /dev/null 2>&1
N=$(Q "SELECT COUNT(*) FROM AuthenticationSubscription WHERE ueid BETWEEN 1010000000300 AND 1010000000349")
echo "== provisioned UEs (IMSI 300-349): ${N:-unknown}"
if [ "${N:-0}" -lt 50 ]; then echo "re-provisioning"; bash ~/provision_paper_ues.sh > /tmp/provision.log 2>&1; fi
kubectl rollout restart deployment/oai-udr -n oai > /dev/null && kubectl rollout status deployment/oai-udr -n oai --timeout=120s
bash ~/full_reset_3slice.sh
echo "== services"
for s in k3s pushgateway-forward prometheus-forward ue-count-exporter qos-latency-exporter closed-loop-history closed-loop-controller ml-predictor predictive-prescaler slice-profile-exporter slice-testbed-watchdog; do printf "%-28s %s\n" $s "$(systemctl is-active $s)"; done
