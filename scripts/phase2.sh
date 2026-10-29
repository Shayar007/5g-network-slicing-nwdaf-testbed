#!/bin/bash
echo "== 1. clean start (UPFs before SMF)"
bash ~/stop_ues.sh
kubectl rollout restart deploy/oai-upf deploy/oai-upf-auto-auto01 -n oai
kubectl rollout restart deploy/osm-oai-upf -n upf-auto-embb
kubectl rollout status deploy/oai-upf -n oai --timeout=120s
kubectl rollout status deploy/oai-upf-auto-auto01 -n oai --timeout=120s
kubectl rollout status deploy/osm-oai-upf -n upf-auto-embb --timeout=120s
sleep 20
echo "== 2. patch SMF config"
kubectl get cm oai-5g-basic -n oai -o yaml > ~/oai-cm-backup_phase2_$(date +%Y%m%d_%H%M).yaml
~/slice-test/slice-env/bin/python3 ~/phase2_patch.py || { echo "PATCH FAILED, nothing changed"; exit 1; }
diff ~/smf_config_orig.yaml ~/smf_config_new.yaml
echo "== 3. restart SMF"
kubectl rollout restart deploy/oai-smf -n oai
kubectl rollout status deploy/oai-smf -n oai --timeout=150s || { echo "SMF ROLLOUT FAILED -> run: bash ~/phase2_rollback.sh"; exit 1; }
sleep 40
echo "associations: $(kubectl logs -n oai deploy/oai-smf -c smf --tail=-1 2>/dev/null | grep -ic 'ASSOCIATION SETUP RESPONSE')"
kubectl logs -n oai deploy/oai-smf -c smf --tail=-1 2>/dev/null | grep -iE "upf-auto|osm-oai-upf|oai-ovf|10\.42\.0\.[0-9]+" | grep -iE "associat|upf node|added" | head -6 | cut -c1-170
echo "== 4. provision test UE 600"
kubectl exec -i -n oai deploy/oai-mysql -- mysql -u root -plinux oai_db 2>/dev/null << 'SQL'
INSERT IGNORE INTO AuthenticationSubscription (ueid, authenticationMethod, encPermanentKey, protectionParameterId, sequenceNumber, authenticationManagementField, algorithmId, encOpcKey) VALUES ('001010000000600','5G_AKA','fec86ba6eb707ed08905757b1bb44b8f','fec86ba6eb707ed08905757b1bb44b8f','{"sqn": "000000000000", "sqnScheme": "NON_TIME_BASED", "lastIndexes": {"ausf": 0}}','8000','milenage','C42449363BBAD02B66D16BC975D77CC1');
INSERT IGNORE INTO SessionManagementSubscriptionData (ueid, servingPlmnid, singleNssai, dnnConfigurations) VALUES ('001010000000600','00101','{"sd": "16777215", "sst": 1}','{"oai-ovf-embb": {"sscModes": {"defaultSscMode": "SSC_MODE_1"}, "sessionAmbr": {"uplink": "1000Mbps", "downlink": "1000Mbps"}, "5gQosProfile": {"5qi": 1, "arp": {"preemptCap": "NOT_PREEMPT", "preemptVuln": "PREEMPTABLE", "priorityLevel": 15}, "priorityLevel": 1}, "pduSessionTypes": {"defaultSessionType": "IPV4"}, "staticIpAddress": [{"ipv4Addr": "12.10.1.10"}]}}');
SQL
echo "== 5. demo UEs first, registry, then test UE"
sudo rm -f /tmp/big-*.log
B=3 SLP=15 bash ~/demo_ramp_big.sh 3 1 2; sleep 45
bash ~/rebuild_registry.sh
sed -e "s/001010000000100/001010000000600/g" -e "s/apn: 'oai'/apn: 'oai-ovf-embb'/" ~/UERANSIM/config/oai-ue.yaml > /tmp/ovf-600.yaml
grep -nE "apn|supi" /tmp/ovf-600.yaml
sudo nohup ~/UERANSIM/build/nr-ue -c /tmp/ovf-600.yaml > /tmp/ovf-600.log 2>&1 &
sleep 35
echo "== 6. verify"
grep -E "Reject|TUN interface" /tmp/ovf-600.log | tail -3 | cut -c1-160
T=$(ip -4 -o addr show | grep "12.10.1.10/" | awk '{print $2}'); echo "iface=[$T]"
[ -n "$T" ] && { ping -I $T -c 3 -W 2 12.10.1.1 | grep "packet loss"; ping -I $T -c 3 -W 2 8.8.8.8 | grep "packet loss"; }
echo "-- new UPF log:"; kubectl logs -n upf-auto-embb deploy/osm-oai-upf --tail=300 2>/dev/null | grep -iE "N4_SESSION|session" | tail -3 | cut -c1-150
echo "-- SMF warnings:"; kubectl logs -n oai deploy/oai-smf -c smf --since=5m 2>/dev/null | grep -iE "No UPF available|UPF could not" | tail -2 | cut -c1-150
kubectl get pods -n oai --no-headers | grep smf | awk '{print "SMF restarts:", $4}'
echo "-- old UEs:"
for i in $(ls /sys/class/net | grep uesimtun); do ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1); case $ip in 12.10.*) continue;; esac; ping -I $i -c 2 -W 2 ${ip%.*}.1 >/dev/null 2>&1 && echo "$ip ok" || echo "$ip fail"; done
