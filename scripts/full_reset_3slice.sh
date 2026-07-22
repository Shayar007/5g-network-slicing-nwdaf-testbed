#!/bin/bash
echo "FULL RESET — SMF + all 3 UPFs"
sudo pkill -9 -f nr-gnb 2>/dev/null
sudo pkill -9 -f nr-ue 2>/dev/null
sleep 3
for tun in $(ip link show 2>/dev/null | grep -oE "uesimtun[0-9]+"); do
    sudo ip link delete "$tun" 2>/dev/null
done
sleep 2

kubectl rollout restart deployment/oai-smf -n oai
kubectl rollout restart deployment/oai-upf -n oai
kubectl rollout restart deployment/oai-upf2 -n oai
kubectl rollout restart deployment/oai-upf-auto-auto01 -n oai
kubectl rollout status deployment/oai-smf -n oai --timeout=90s
kubectl rollout status deployment/oai-upf -n oai --timeout=90s
kubectl rollout status deployment/oai-upf2 -n oai --timeout=90s
kubectl rollout status deployment/oai-upf-auto-auto01 -n oai --timeout=90s

echo "Waiting 40s for fresh UPF associations..."
sleep 40
kubectl logs -n oai deploy/oai-smf --since=2m | grep "Associate with UPF profile"

AMF_IP=$(kubectl get pod -n oai -l app.kubernetes.io/name=oai-amf -o jsonpath='{.items[0].status.podIP}')
sed -i "s/address: .*/address: $AMF_IP/" ~/UERANSIM/config/oai-gnb.yaml
sudo ~/UERANSIM/build/nr-gnb -c ~/UERANSIM/config/oai-gnb.yaml > /tmp/reset_gnb.log 2>&1 &
for i in $(seq 1 15); do grep -q "NG Setup procedure is successful" /tmp/reset_gnb.log 2>/dev/null && echo "gNB up after ${i}s" && break; sleep 1; done

sudo ~/UERANSIM/build/nr-ue -c ~/UERANSIM/config/oai-ue.yaml > /tmp/reset_embb.log 2>&1 &
for i in $(seq 1 20); do grep -q "PDU Session establishment is successful" /tmp/reset_embb.log 2>/dev/null && echo "eMBB confirmed after ${i}s" && break; sleep 1; done
sleep 3

sudo ~/UERANSIM/build/nr-ue -c ~/ue-urllc-WORKING.yaml > /tmp/reset_urllc.log 2>&1 &
for i in $(seq 1 20); do grep -q "PDU Session establishment is successful" /tmp/reset_urllc.log 2>/dev/null && echo "URLLC confirmed after ${i}s" && break; sleep 1; done
sleep 3

sudo ~/UERANSIM/build/nr-ue -c ~/ue-mmtc-WORKING.yaml > /tmp/reset_mmtc.log 2>&1 &
for i in $(seq 1 20); do grep -q "PDU Session establishment is successful" /tmp/reset_mmtc.log 2>/dev/null && echo "mMTC confirmed after ${i}s" && break; sleep 1; done
sleep 3

echo ""
printf "%-8s %-12s %-15s %s\n" "SLICE" "TUN" "IP" "PING"
for entry in "eMBB:/tmp/reset_embb.log" "URLLC:/tmp/reset_urllc.log" "mMTC:/tmp/reset_mmtc.log"; do
    NAME="${entry%%:*}"; LOG="${entry##*:}"
    SUCCESS=$(grep -c "PDU Session establishment is successful" "$LOG" 2>/dev/null)
    TUN=$(grep -oE "uesimtun[0-9]+" "$LOG" 2>/dev/null | head -1)
    if [ "$SUCCESS" -ge 1 ] && [ -n "$TUN" ]; then
        IP=$(ip addr show "$TUN" 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1)
        SUBNET=$(echo "$IP" | cut -d. -f1-3)
        RESULT=$(ping -I "$TUN" -c 2 -W 2 "${SUBNET}.1" 2>&1 | grep -oE "[0-9]+% packet loss")
        printf "%-8s %-12s %-15s %s\n" "$NAME" "$TUN" "$IP" "$RESULT"
    else
        printf "%-8s %-12s %-15s %s\n" "$NAME" "-" "-" "PDU SESSION FAILED"
    fi
done
