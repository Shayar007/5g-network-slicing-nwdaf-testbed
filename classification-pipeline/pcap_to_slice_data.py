"""
pcap_to_slice_data.py - subnet-based slice classification (robust, no NGAP ASN.1 parsing needed)
"""
import argparse, csv, subprocess, sys

SLICE_SUBNETS = {
    "12.1.1.": ("eMBB", 1),
    "12.2.1.": ("URLLC", 3),
    "12.3.1.": ("mMTC", 9),
}

def run_tshark(pcap_path, display_filter, fields):
    cmd = ["tshark", "-r", pcap_path, "-Y", display_filter, "-T", "fields"]
    for f in fields:
        cmd += ["-e", f]
    cmd += ["-E", "separator=,", "-E", "header=y", "-E", "occurrence=l"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        sys.exit("tshark not found. Install: sudo apt install tshark")
    except subprocess.CalledProcessError as e:
        sys.exit(f"tshark failed on {pcap_path}:\n{e.stderr}")
    lines = result.stdout.strip().splitlines()
    if len(lines) < 2:
        return []
    header = lines[0].split(",")
    return [dict(zip(header, line.split(","))) for line in lines[1:]]

def classify(inner_ip):
    for prefix, (slice_name, five_qi) in SLICE_SUBNETS.items():
        if inner_ip and inner_ip.startswith(prefix):
            return slice_name, five_qi
    return None, None

def build_dataset(gtpu_pcap, out_csv):
    fields = ["frame.time_epoch", "frame.len", "ip.src", "gtp.teid"]
    rows = run_tshark(gtpu_pcap, "gtp", fields)
    if not rows:
        print("WARNING: no GTP-U packets found.", file=sys.stderr)
    written = 0
    counts = {}
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["five_qi", "packet_size_bytes", "throughput_kbps", "timestamp", "teid", "slice"])
        writer.writeheader()
        for row in rows:
            slice_name, five_qi = classify(row.get("ip.src"))
            if slice_name is None:
                continue
            try:
                pkt_bytes = float(row.get("frame.len", 0) or 0)
            except ValueError:
                pkt_bytes = 0
            throughput_kbps = round((pkt_bytes * 8) / 1000, 2)
            writer.writerow({
                "five_qi": five_qi,
                "packet_size_bytes": pkt_bytes,
                "throughput_kbps": throughput_kbps,
                "timestamp": row.get("frame.time_epoch", ""),
                "teid": row.get("gtp.teid", ""),
                "slice": slice_name,
            })
            counts[slice_name] = counts.get(slice_name, 0) + 1
            written += 1
    print(f"Wrote {written} rows to {out_csv}")
    print(f"Per-slice breakdown: {counts}")
#def build_dataset(gtpu_pcap, out_csv):
#    fields = ["frame.time_epoch", "frame.len", "ip.src", "gtp.teid"]
#    rows = run_tshark(gtpu_pcap, "gtp", fields)
#    if not rows:
#        print("WARNING: no GTP-U packets found.", file=sys.stderr)

#    written = 0
#    counts = {}
#    with open(out_csv, "w", newline="") as f:
#        writer = csv.DictWriter(f, fieldnames=["five_qi", "packet_size_bytes", "timestamp", "teid", "slice"])
#        writer.writeheader()
#        for row in rows:
#            slice_name, five_qi = classify(row.get("ip.src"))
#            if slice_name is None:
#                continue
#            writer.writerow({
#                "five_qi": five_qi,
#                "packet_size_bytes": row.get("frame.len", ""),
#                "timestamp": row.get("frame.time_epoch", ""),
#                "teid": row.get("gtp.teid", ""),
#                "slice": slice_name,
#            })
#            counts[slice_name] = counts.get(slice_name, 0) + 1
#            written += 1

#    print(f"Wrote {written} rows to {out_csv}")
#    print(f"Per-slice breakdown: {counts}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gtpu", required=True, help="Path to GTP-U pcap")
    parser.add_argument("--out", default="real_5qi_data.csv", help="Output CSV path")
    args = parser.parse_args()
    build_dataset(args.gtpu, args.out)
