#!/bin/bash
# usage: upf_chain.sh N   (N=3,4,... a number not used before)
N=${1:?usage: upf_chain.sh N}
VIM=d99f1603-cd89-4efd-8177-aab4c134bb59
NS=upf-auto-embb$N; DNN=oai-ovf-embb$N; SUBN=$((20+N)); IMSI=$(printf "001010000000%03d" $((600+N)))
IP=12.$SUBN.1.10; GW=12.$SUBN.1.1
echo "== 1. create UPF $NS via OSM (sst 1, sd FFFFFF, DNN $DNN, subnet 12.$SUBN.1.0/24)"
osm ns-list 2>/dev/null | grep -q "$NS " && { echo "NS $NS already exists, use a new N"; exit 1; }
kubectl create namespace $NS --dry-run=client -o yaml | kubectl apply -f - >/dev/null
t0=$(date +%s)
osm ns-create --ns_name $NS --nsd_name osm_oai_upf_ns --vim_account $VIM --config "{additionalParamsForVnf: [{member-vnf-index: upf_vnf, additionalParamsForKdu: [{kdu_name: upfapp, k8s-namespace: $NS, additionalParams: {smfHost: oai-smf.oai.svc.cluster.local, nrfHost: oai-nrf.oai.svc.cluster.local, sst: 1, sd: \"FFFFFF\", dnn: $DNN, ipv4Subnet: \"12.$SUBN.1.0/24\"}}]}]}" >/dev/null
for i in $(seq 1 40); do st=$(osm ns-list 2>/dev/null | grep "$NS " | grep -oE "READY|BROKEN" | head -1); [ -n "$st" ] && break; sleep 5; done
echo "NS state: ${st:-TIMEOUT} after $(( $(date +%s)-t0 ))s"
[ "$st" = READY ] || { echo "UPF creation failed, stopping (nothing else changed)"; exit 1; }
echo "== 2. add the UPF to the SMF config (backup first)"
kubectl get cm oai-5g-basic -n oai -o yaml > ~/oai-cm-backup_chain${N}_$(date +%H%M).yaml
N=$N NS=$NS DNN=$DNN SUBN=$SUBN python3 - << 'PY' || { echo "config patch failed, nothing changed"; exit 1; }
import json, os, subprocess
cur = subprocess.run(["kubectl","get","cm","oai-5g-basic","-n","oai","-o","jsonpath={.data.config\\.yaml}"],capture_output=True,text=True).stdout
ns, dnn, subn = os.environ["NS"], os.environ["DNN"], os.environ["SUBN"]
a1 = "    - host: oai-upf2\n"; a2 = '    ipv4_subnet: "12.3.1.0/24"\n'
assert cur.count(a1) == 1 and cur.count(a2) == 1, "anchors not found once"
assert dnn not in cur, "already in config"
upf = (f"    - host: osm-oai-upf.{ns}.svc.cluster.local\n      config:\n        enable_usage_reporting: no\n"
       f"      upf_info:\n        sNssaiUpfInfoList:\n        - sNssai: *embb_slice\n          dnnUpfInfoList:\n            - dnn: {dnn}\n")
dn = f'  - dnn: "{dnn}"\n    pdu_session_type: "IPV4"\n    ipv4_subnet: "12.{subn}.1.0/24"\n'
out = cur.replace(a1, upf + a1).replace(a2, a2 + dn)
subprocess.run(["kubectl","patch","cm","oai-5g-basic","-n","oai","--type","merge","-p",json.dumps({"data":{"config.yaml":out}})],check=True)
print("SMF config patched")
PY
echo "== 3. provision UE $IMSI on DNN $DNN ($IP)"
kubectl exec -i -n oai deploy/oai-mysql -- mysql -u root -plinux oai_db 2>/dev/null << SQL
INSERT IGNORE INTO AuthenticationSubscription (ueid, authenticationMethod, encPermanentKey, protectionParameterId, sequenceNumber, authenticationManagementField, algorithmId, encOpcKey) VALUES ('$IMSI','5G_AKA','fec86ba6eb707ed08905757b1bb44b8f','fec86ba6eb707ed08905757b1bb44b8f','{"sqn": "000000000000", "sqnScheme": "NON_TIME_BASED", "lastIndexes": {"ausf": 0}}','8000','milenage','C42449363BBAD02B66D16BC975D77CC1');
INSERT IGNORE INTO SessionManagementSubscriptionData (ueid, servingPlmnid, singleNssai, dnnConfigurations) VALUES ('$IMSI','00101','{"sd": "16777215", "sst": 1}','{"$DNN": {"sscModes": {"defaultSscMode": "SSC_MODE_1"}, "sessionAmbr": {"uplink": "1000Mbps", "downlink": "1000Mbps"}, "5gQosProfile": {"5qi": 1, "arp": {"preemptCap": "NOT_PREEMPT", "preemptVuln": "PREEMPTABLE", "priorityLevel": 15}, "priorityLevel": 1}, "pduSessionTypes": {"defaultSessionType": "IPV4"}, "staticIpAddress": [{"ipv4Addr": "$IP"}]}}');
SQL
sed -e "s/001010000000100/$IMSI/g" -e "s/apn: 'oai'/apn: '$DNN'/" ~/UERANSIM/config/oai-ue.yaml > /tmp/chain-$N.yaml
echo "== 4. clean restart: UEs stop, ALL UPFs first, then SMF"
bash ~/stop_ues.sh
kubectl rollout restart deploy/oai-upf deploy/oai-upf-auto-auto01 -n oai
for ns in $(kubectl get ns --no-headers | awk '{print $1}' | grep '^upf-auto-embb'); do kubectl rollout restart deploy/osm-oai-upf -n $ns; done
kubectl rollout status deploy/oai-upf -n oai --timeout=120s; kubectl rollout status deploy/oai-upf-auto-auto01 -n oai --timeout=120s
for ns in $(kubectl get ns --no-headers | awk '{print $1}' | grep '^upf-auto-embb'); do kubectl rollout status deploy/osm-oai-upf -n $ns --timeout=120s; done
sleep 20
kubectl rollout restart deploy/oai-smf -n oai; kubectl rollout status deploy/oai-smf -n oai --timeout=150s; sleep 40
echo "associations: $(kubectl logs -n oai deploy/oai-smf -c smf --tail=-1 2>/dev/null | grep -ic 'ASSOCIATION SETUP RESPONSE')"
echo "== 5. demo UEs (one at a time), registry, then the new UE"
sudo rm -f /tmp/big-*.log
B=1 SLP=20 bash ~/demo_ramp_big.sh 3 1 2; sleep 45
bash ~/rebuild_registry.sh
sudo nohup ~/UERANSIM/build/nr-ue -c /tmp/chain-$N.yaml > /tmp/chain-$N.log 2>&1 &
sleep 35
echo "== 6. verify"
grep -E "Reject|TUN interface" /tmp/chain-$N.log | tail -2 | cut -c1-150
T=$(ip -4 -o addr show | grep "$IP/" | awk '{print $2}'); echo "iface=[$T]"
[ -n "$T" ] && { ping -I $T -c 3 -W 2 $GW | grep "packet loss"; ping -I $T -c 3 -W 2 8.8.8.8 | grep "packet loss"; }
echo "-- session on the new UPF:"; kubectl logs -n $NS deploy/osm-oai-upf --tail=300 2>/dev/null | grep -iE "N4_SESSION" | tail -2 | cut -c1-140
for i in $(ls /sys/class/net | grep uesimtun); do ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1); ping -I $i -c 2 -W 2 ${ip%.*}.1 >/dev/null 2>&1 && echo "$ip ok" || echo "$ip fail"; done
kubectl get pods -n oai --no-headers | grep smf | awk '{print "SMF restarts:", $4}'
