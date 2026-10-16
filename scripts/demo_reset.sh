#!/bin/bash
sudo systemctl stop closed-loop-controller
rm -f ~/slice-test/closed_loop_history.db.policy_state.json
bash ~/full_reset_3slice.sh
echo "settling 90s so QoS probes are clean before the controller starts..."
sleep 90
sudo systemctl start closed-loop-controller
echo "waiting for mmtc actual=1 and ML forecast<=1.3 (max 10 min)..."
OK=0
for i in $(seq 1 40); do
  A=$(curl -sg 'localhost:9090/api/v1/query?query=slice_ue_count{slice="mmtc"}' | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 99)")
  P=$(curl -sg 'localhost:9090/api/v1/query?query=slice_ue_count_predicted{slice="mmtc"}' | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; print(r[0]['value'][1] if r else 99)")
  python3 -c "import sys; sys.exit(0 if float('$A')<=1 and float('$P')<=1.3 else 1)" && OK=1 && break
  sleep 15
done
if [ $OK = 1 ]; then echo "READY: mmtc actual=$A predicted=$P, controller state cleared"; else echo "NOT READY: mmtc actual=$A predicted=$P (ghost UEs? see: ~/UERANSIM/build/nr-cli --dump)"; fi
