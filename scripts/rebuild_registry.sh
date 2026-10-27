#!/bin/bash
R=~/slice-test/ue_registry_50.csv
cp $R $R.bak_$(date +%H%M%S)
{ echo "name,imsi,slice,tun,ip"
for i in $(ls /sys/class/net | grep uesimtun); do
  ip=$(ip -4 -o addr show $i | awk '{print $4}' | cut -d/ -f1)
  case $ip in 12.1.*) s=eMBB;; 12.2.*) s=URLLC;; 12.3.*) s=mMTC;; *) continue;; esac
  echo "ue-$i,0,$s,$i,$ip"
done; } > /tmp/reg.csv && cp /tmp/reg.csv $R
sudo systemctl restart qos-latency-exporter; echo "registry rows: $(($(wc -l < $R)-1))"
