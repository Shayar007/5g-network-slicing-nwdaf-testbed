#!/bin/bash
sudo pkill -f 'demo-embb-[3-9]'; sudo pkill -f 'demo-urllc-[12]'; sudo pkill -f 'demo-mmtc-[2-4]'
echo "$(date +%T) ramp-down: eMBB 10->3, URLLC 3->1, mMTC 5->2 UEs"
