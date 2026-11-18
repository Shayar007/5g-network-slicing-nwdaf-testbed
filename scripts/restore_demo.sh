#!/bin/bash
echo "== 1. stop UEs"; bash ~/stop_ues.sh
echo "== 2. gNB -> AMF IP check"
AMF=$(kubectl get pod -n oai -l app.kubernetes.io/name=oai-amf -o jsonpath='{.items[0].status.podIP}')
grep -q "address: $AMF" ~/UERANSIM/config/oai-gnb.yaml && pgrep -x nr-gnb >/dev/null && echo "gNB ok" || bash ~/fix_gnb_after_reboot.sh
echo "== 3. UPFs first (all), then SMF"
kubectl rollout restart deploy/oai-upf deploy/oai-upf-auto-auto01 -n oai
NSS=$(kubectl get ns --no-headers | awk '{print $1}' | grep '^upf-auto-embb')
for ns in $NSS; do kubectl rollout restart deploy/osm-oai-upf -n $ns; done
kubectl rollout status deploy/oai-upf -n oai --timeout=120s
kubectl rollout status deploy/oai-upf-auto-auto01 -n oai --timeout=120s
for ns in $NSS; do kubectl rollout status deploy/osm-oai-upf -n $ns --timeout=120s; done
sleep 20
kubectl rollout restart deploy/oai-smf -n oai; kubectl rollout status deploy/oai-smf -n oai --timeout=150s; sleep 40
echo "associations: $(kubectl logs -n oai deploy/oai-smf -c smf --tail=-1 2>/dev/null | grep -ic 'ASSOCIATION SETUP RESPONSE')"
echo "== 4. demo UEs (one at a time), registry, then extra UEs"
sudo rm -f /tmp/big-*.log
B=1 SLP=20 bash ~/demo_ramp_big.sh 3 1 2; sleep 45
bash ~/rebuild_registry.sh
for y in /tmp/ovf-600.yaml /tmp/chain-3.yaml; do
  [ -f $y ] && { sudo nohup ~/UERANSIM/build/nr-ue -c $y > $y.log 2>&1 & sleep 25; }
done
echo "== 5. verify"
sleep 20
for i in $(ls /sys/class/net | grep uesimtun); do ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1); ping -I $i -c 2 -W 2 ${ip%.*}.1 >/dev/null 2>&1 && echo "$ip ok" || echo "$ip fail"; done
sleep 30
sudo journalctl -u qos-latency-exporter --since "1 min ago" -o cat | grep -E "eMBB|URLLC|mMTC" | tail -3
sudo journalctl -u closed-loop-controller --since "1 min ago" -o cat | tail -3 | cut -c1-140
for ns in echo-embb echo-urllc 247eeb81-5df2-4187-95c8-18d9ae13ca19; do kubectl get deploy scalable-echo -n $ns --no-headers; done
kubectl get pods -n oai --no-headers | grep smf | awk '{print "SMF restarts:", $4}'
