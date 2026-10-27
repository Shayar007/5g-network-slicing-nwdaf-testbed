#!/bin/bash
n=0
for f in $(grep -L 'TUN interface' /tmp/big-*.log); do
  b=$(basename $f .log)
  sudo pkill -f "$b\.yaml" 2>/dev/null
  sudo nohup ~/UERANSIM/build/nr-ue -c /tmp/$b.yaml > /tmp/$b.log 2>&1 &
  n=$((n+1)); [ $((n%4)) -eq 0 ] && sleep 20
done
echo "relaunched $n"
