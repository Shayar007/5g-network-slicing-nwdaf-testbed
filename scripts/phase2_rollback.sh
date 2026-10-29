#!/bin/bash
bash ~/stop_ues.sh
~/slice-test/slice-env/bin/python3 -c "
import json,subprocess
t=open('/home/comm/smf_config_orig.yaml').read()
subprocess.run(['kubectl','patch','cm','oai-5g-basic','-n','oai','--type','merge','-p',json.dumps({'data':{'config.yaml':t}})],check=True)
print('configmap restored')"
kubectl rollout restart deploy/oai-upf deploy/oai-upf-auto-auto01 -n oai
kubectl rollout status deploy/oai-upf -n oai --timeout=120s; kubectl rollout status deploy/oai-upf-auto-auto01 -n oai --timeout=120s; sleep 20
kubectl rollout restart deploy/oai-smf -n oai; kubectl rollout status deploy/oai-smf -n oai --timeout=150s; sleep 40
B=3 SLP=15 bash ~/demo_ramp_big.sh 3 1 2; sleep 60; bash ~/rebuild_registry.sh; sleep 40
for i in $(ls /sys/class/net | grep uesimtun); do ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1); ping -I $i -c 2 -W 2 ${ip%.*}.1 >/dev/null 2>&1 && echo "$ip ok" || echo "$ip fail"; done
