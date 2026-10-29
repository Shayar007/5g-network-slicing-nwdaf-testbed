#!/bin/bash
AMF_IP=$(kubectl get pod -n oai -l app.kubernetes.io/name=oai-amf -o jsonpath='{.items[0].status.podIP}')
[ -z "$AMF_IP" ] && { echo "AMF IP empty, aborting"; exit 1; }
[ "$(grep -c 'address:' ~/UERANSIM/config/oai-gnb.yaml)" = "1" ] || { echo "address lines != 1, aborting"; exit 1; }
sed -i "s/address: .*/address: $AMF_IP/" ~/UERANSIM/config/oai-gnb.yaml
sudo pkill -f nr-gnb; sleep 3
sudo nohup ~/UERANSIM/build/nr-gnb -c ~/UERANSIM/config/oai-gnb.yaml > /tmp/reset_gnb.log 2>&1 &
for i in $(seq 1 20); do grep -q "NG Setup procedure is successful" /tmp/reset_gnb.log 2>/dev/null && echo "gNB up (AMF $AMF_IP)" && exit 0; sleep 1; done
echo "gNB did not connect"; exit 1
