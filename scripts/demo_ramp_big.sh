#!/bin/bash
# usage: demo_ramp_big.sh N_EMBB N_URLLC N_MMTC   (max 100/50/100)
B=${B:-5}; SLP=${SLP:-30}
NE=${1:-50}; NU=${2:-15}; NM=${3:-25}
TPL_EMBB=~/UERANSIM/config/oai-ue.yaml; BASE_EMBB=001010000000100
TPL_URLLC=~/ue-urllc-WORKING.yaml;      BASE_URLLC=001010000000325
TPL_MMTC=~/ue-mmtc-WORKING.yaml;        BASE_MMTC=001010000000340
sudo pkill -f 'big-(embb|urllc|mmtc)-[0-9]' 2>/dev/null
start() { # slice tpl base idx k
  local imsi=$(printf "%015d" $((1010000000000 + $4)))
  sed "s/$3/$imsi/g" "$2" > /tmp/big-$1-$5.yaml
  sudo nohup ~/UERANSIM/build/nr-ue -c /tmp/big-$1-$5.yaml > /tmp/big-$1-$5.log 2>&1 &
}
guard() { local a=$(free -m | awk '/Mem:/{print $7}'); [ "$a" -lt 2500 ] && { echo "STOP: only ${a}MB available"; exit 1; }; }
max=$(( NE > NU ? (NE > NM ? NE : NM) : (NU > NM ? NU : NM) ))
for ((k=0; k<max; k+=B)); do
  guard
  for ((j=k; j<k+B; j++)); do
    [ $j -lt $NE ] && start embb  $TPL_EMBB  $BASE_EMBB  $((350+j)) $j
    [ $j -lt $NU ] && start urllc $TPL_URLLC $BASE_URLLC $((450+j)) $j
    [ $j -lt $NM ] && start mmtc  $TPL_MMTC  $BASE_MMTC  $((500+j)) $j
  done
  echo "$(date +%T) batch done up to $((k+B)) | load $(cut -d' ' -f1 /proc/loadavg) | avail $(free -m | awk '/Mem:/{print $7}')MB"
  sleep $SLP
done
echo "ramp done: eMBB=$NE URLLC=$NU mMTC=$NM"
