#!/bin/bash
echo "slice  namespace                                replicas"
for p in "mmtc 247eeb81-5df2-4187-95c8-18d9ae13ca19" "embb echo-embb" "urllc echo-urllc"; do
  set -- $p
  printf "%-6s %-40s %s\n" $1 $2 "$(kubectl get deploy scalable-echo -n $2 --no-headers 2>/dev/null | awk '{print $2}')"
done
