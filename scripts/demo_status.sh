#!/bin/bash
~/UERANSIM/build/nr-cli --dump 2>/dev/null | python3 -c "
import re,sys
n={'eMBB':0,'URLLC':0,'mMTC':0}
for l in sys.stdin:
    m=re.search(r'imsi-\d{12}(\d{3})',l)
    if not m: continue
    s=int(m.group(1))
    if s in (100,105,109,200,201,202) or 300<=s<=324: n['eMBB']+=1
    elif s==225 or 325<=s<=339: n['URLLC']+=1
    elif s==240 or 340<=s<=349: n['mMTC']+=1
print('registered UEs (RAN):', n)"
for s in embb urllc mmtc; do curl -sg "localhost:9090/api/v1/query?query=slice_ue_count{slice=\"$s\"}" | python3 -c "import json,sys; r=json.load(sys.stdin)['data']['result']; print('$s exporter:', r[0]['value'][1] if r else 'n/a')"; done
