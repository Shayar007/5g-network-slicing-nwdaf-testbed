#!/bin/bash
VIM=d99f1603-cd89-4efd-8177-aab4c134bb59
PY=~/slice-test/slice-env/bin/python3
DROP=/etc/systemd/system/closed-loop-controller.service.d/osm-slices.conf
cd ~/slice-test/closed-loop-controller || exit 1
sudo rm -f $DROP; sudo systemctl daemon-reload; sudo systemctl restart closed-loop-controller

if ! grep -q 'OSM_KDU_NS_{slice_name.upper()}' mano_orchestrator.py; then
  cp mano_orchestrator.py mano_orchestrator.py.bak4_20261016
  $PY - << 'PYEOF' || exit 1
p = "mano_orchestrator.py"
s = open(p).read()
old = '        kns = os.getenv("OSM_KDU_NS", "")'
assert s.count(old) == 1, "anchor not found exactly once"
open(p, "w").write(s.replace(old, '        kns = os.getenv(f"OSM_KDU_NS_{slice_name.upper()}") or os.getenv("OSM_KDU_NS", "")'))
print("patched")
PYEOF
fi
$PY -c "import ast; ast.parse(open('mano_orchestrator.py').read()); print('syntax ok')" || exit 1

mkns() {
  s=$1
  kubectl create namespace echo-$s --dry-run=client -o yaml | kubectl apply -f - > /dev/null
  osm ns-create --ns_name scalable-echo-$s --nsd_name scalable_echo_ns --vim_account $VIM \
    --config "{additionalParamsForVnf: [{member-vnf-index: echo_vnf, additionalParamsForKdu: [{kdu_name: echoapp, k8s-namespace: echo-$s}]}]}" || return 1
  st=""
  for i in $(seq 1 30); do
    st=$(osm ns-list 2>/dev/null | grep "scalable-echo-$s " | grep -oE "READY|BROKEN" | head -1)
    [ -n "$st" ] && break
    sleep 10
  done
  echo "scalable-echo-$s: ${st:-TIMEOUT}"
  [ "$st" = READY ] || return 1
  kubectl get deploy scalable-echo -n echo-$s --no-headers
}
test_slice() {
  s=$1; U=$(echo $s | tr a-z A-Z)
  out=$(env OSM_NS_$U=scalable-echo-$s OSM_KDU_NS_$U=echo-$s $PY -c "
from mano_orchestrator import OSMBackend
b = OSMBackend(); b.scale_slice('$s', 'up'); b.scale_slice('$s', 'down')" 2>&1)
  echo "$out" | grep -E "converged|FAILED|skipped|Error"
  [ "$(echo "$out" | grep -c converged)" = 2 ]
}
mkns embb && mkns urllc || { echo "STOP: NS nahi bana, controller ko chhua nahi gaya. Cleanup: osm ns-delete scalable-echo-embb --force; osm ns-delete scalable-echo-urllc --force; kubectl delete ns echo-embb echo-urllc --ignore-not-found"; exit 1; }
test_slice embb && test_slice urllc || { echo "STOP: manual scale test fail, controller ko chhua nahi gaya"; exit 1; }

sudo tee $DROP > /dev/null << 'ENVEOF'
[Service]
Environment=OSM_NS_EMBB=scalable-echo-embb
Environment=OSM_NS_URLLC=scalable-echo-urllc
Environment=OSM_KDU_NS_EMBB=echo-embb
Environment=OSM_KDU_NS_URLLC=echo-urllc
ENVEOF
cp mano_orchestrator.py ~/slice-testbed-repo/closed-loop-controller/
sudo systemctl daemon-reload && sudo systemctl restart closed-loop-controller
sleep 3
systemctl show closed-loop-controller -p Environment | tr ' ' '\n' | grep -E "OSM_NS|OSM_KDU_NS|MANO_BACKEND|DEMO_DRY_RUN"
echo "DONE: eMBB, URLLC aur mMTC teeno ab OSM se scale honge"
