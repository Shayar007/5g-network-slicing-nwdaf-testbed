#!/bin/bash
CLI=~/UERANSIM/build/nr-cli
for u in $($CLI --dump | grep '^imsi-'); do $CLI $u --exec "deregister normal" >/dev/null 2>&1; done
sleep 20
sudo pkill -f nr-ue; sleep 5
for t in $(ls /sys/class/net | grep uesimtun); do sudo ip link del $t 2>/dev/null; done
for p in $(ip rule show | grep rt_uesimtun | awk -F: '{print $1}'); do sudo ip rule del pref $p 2>/dev/null; done
echo "tun left: $(ls /sys/class/net | grep -c uesimtun)"
