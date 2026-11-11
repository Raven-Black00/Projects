import csv, time, os, requests

IP = "192.168.1.50"          # your Gamma Hex
URL = f"http://{IP}/api/system/info"
FILE = "hex_log.csv"
ASIC_MAX, VR_MAX = 70, 80    # soft limits in C
FIELDS = ["ts","freq","vcore","hash_ghs","power_w","asic_c","vr_c","jth"]

new = not os.path.exists(FILE)
with open(FILE, "a", newline="") as f:
    w = csv.writer(f)
    if new: w.writerow(FIELDS)
    while True:
        try:
            d = requests.get(URL, timeout=5).json()
            hs, p = d["hashRate"], d["power"]
            jth = p / (hs / 1000) if hs else 0
            w.writerow([int(time.time()), d["frequency"], d["coreVoltage"],
                        round(hs,1), round(p,1), d["temp"], d["vrTemp"], round(jth,2)])
            f.flush()
            if d["temp"] > ASIC_MAX or d["vrTemp"] > VR_MAX:
                print("OVER TEMP - cut clock or power now!", d["temp"], d["vrTemp"])
        except Exception as e:
            print("poll failed:", e)
        time.sleep(10)