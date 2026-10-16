#!/bin/bash
TPL_EMBB=~/UERANSIM/config/oai-ue.yaml; BASE_EMBB=001010000000100
TPL_URLLC=~/ue-urllc-WORKING.yaml;      BASE_URLLC=001010000000325
TPL_MMTC=~/ue-mmtc-WORKING.yaml;        BASE_MMTC=001010000000340
kh() { grep -hE "^\s*(key|op|opType|amf):" "$1" | md5sum | cut -c1-8; }
echo "auth-hash embb=$(kh $TPL_EMBB) urllc=$(kh $TPL_URLLC) mmtc=$(kh $TPL_MMTC)  (teeno same hone chahiye)"
sudo pkill -f 'demo-(embb|urllc|mmtc)-[0-9]' 2>/dev/null
start() {
  sed "s/$3/$4/g" "$2" > /tmp/demo-$1-$5.yaml
  sudo nohup ~/UERANSIM/build/nr-ue -c /tmp/demo-$1-$5.yaml > /tmp/demo-$1-$5.log 2>&1 &
  echo "$(date +%H:%M:%S) +UE $1 imsi=$4"
}
for i in 1 2 3 4 5 6 7 8 9; do
  sleep 25
  start embb $TPL_EMBB $BASE_EMBB $(printf "%015d" $((1010000000300 + i))) $i
  if [ $((i % 2)) -eq 0 ]; then k=$((i / 2)); start mmtc $TPL_MMTC $BASE_MMTC $(printf "%015d" $((1010000000340 + k))) $k; fi
  if [ $((i % 4)) -eq 0 ]; then k=$((i / 4)); start urllc $TPL_URLLC $BASE_URLLC $(printf "%015d" $((1010000000325 + k))) $k; fi
done
echo "ramp done: target eMBB=10, URLLC=3, mMTC=5"
