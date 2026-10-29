import json, subprocess, sys
def sh(*a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode: sys.exit(r.stderr)
    return r.stdout
cur = sh("kubectl","get","cm","oai-5g-basic","-n","oai","-o","jsonpath={.data.config\\.yaml}")
open("/home/comm/smf_config_orig.yaml","w").write(cur)
if "oai-ovf-embb" in cur: sys.exit("already patched")
a1 = "    - host: oai-upf2\n"
a2 = '    ipv4_subnet: "12.3.1.0/24"\n'
assert cur.count(a1) == 1, "upfs anchor count %d" % cur.count(a1)
assert cur.count(a2) == 1, "dnns anchor count %d" % cur.count(a2)
new_upf = ("    - host: osm-oai-upf.upf-auto-embb.svc.cluster.local\n"
           "      config:\n        enable_usage_reporting: no\n"
           "      upf_info:\n        sNssaiUpfInfoList:\n        - sNssai: *embb_slice\n"
           "          dnnUpfInfoList:\n            - dnn: oai-ovf-embb\n")
new_dnn = ('  - dnn: "oai-ovf-embb"\n    pdu_session_type: "IPV4"\n    ipv4_subnet: "12.10.1.0/24"\n')
out = cur.replace(a1, new_upf + a1).replace(a2, a2 + new_dnn)
open("/home/comm/smf_config_new.yaml","w").write(out)
sh("kubectl","patch","cm","oai-5g-basic","-n","oai","--type","merge","-p",json.dumps({"data":{"config.yaml":out}}))
print("patched; original saved to ~/smf_config_orig.yaml")
