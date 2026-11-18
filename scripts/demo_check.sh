#!/bin/bash
ok(){ echo "  OK   $1"; }; bad(){ echo "  FAIL $1"; }
echo "== pods"; n=$(kubectl get pods -A --no-headers | grep -vE "Running|Completed" | wc -l); [ "$n" = 0 ] && ok "all pods running" || bad "$n pods not running"
echo "== core restarts"; kubectl get pods -n oai --no-headers | grep -E "smf|amf|nrf|upf" | awk '{print "  ",$1,"restarts="$4}'
echo "== services"; for s in k3s ue-count-exporter qos-latency-exporter closed-loop-controller ml-predictor slice-testbed-watchdog; do [ "$(systemctl is-active $s)" = active ] && ok $s || bad $s; done
echo "== gNB"; AMF=$(kubectl get pod -n oai -l app.kubernetes.io/name=oai-amf -o jsonpath='{.items[0].status.podIP}'); grep -q "address: $AMF" ~/UERANSIM/config/oai-gnb.yaml && pgrep -x nr-gnb >/dev/null && ok "gNB -> AMF $AMF" || bad "gNB (run fix_gnb_after_reboot.sh)"
echo "== UEs"; for i in $(ls /sys/class/net | grep uesimtun); do ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1); ping -I $i -c 1 -W 2 ${ip%.*}.1 >/dev/null 2>&1 && ok "$ip" || bad "$ip"; done
echo "== probe"; sudo journalctl -u qos-latency-exporter --since "2 min ago" -o cat | grep -E "eMBB|URLLC|mMTC" | tail -3
echo "== controller"; sudo journalctl -u closed-loop-controller --since "2 min ago" -o cat | tail -3 | cut -c1-120
echo "== echo replicas (want 1/1 each)"; for ns in echo-embb echo-urllc 247eeb81-5df2-4187-95c8-18d9ae13ca19; do kubectl get deploy scalable-echo -n $ns --no-headers | awk '{print "  ",$2}'; done
echo "== OSM"; osm ns-list 2>/dev/null | grep -E "scalable-echo|upf-" | awk -F'|' '{print "  ",$2,$5}'
