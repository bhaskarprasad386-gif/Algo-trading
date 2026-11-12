#!/usr/bin/env bash
# Read-only Angel One TLS path diagnostics. Run as root on the VPS.
# Does not change routes, MTU, firewall, sysctl, or trading services.
set -u

HOST="${ANGEL_MASTER_HOST:-margincalculator.angelbroking.com}"
IPS=(13.234.161.154 13.206.217.103 13.233.4.92)
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${TMPDIR:-/tmp}/angelone-tls-diagnostic-$STAMP"
mkdir -p "$OUT"

{
  echo "utc=$(date -u --iso-8601=seconds)"
  echo "host=$HOST"
  echo "kernel=$(uname -sr)"
  echo "openssl=$(openssl version)"
  echo "curl=$(curl --version | head -1)"
  echo "--- interface ---"
  ip -brief link
  echo "--- route ---"
  ip -4 route get "${IPS[0]}"
  echo "--- MTU / TCP MTU probing ---"
  ip -o link show
  sysctl net.ipv4.tcp_mtu_probing 2>/dev/null || true
  echo "--- PMTU trace (if tracepath is installed) ---"
  if command -v tracepath >/dev/null 2>&1; then tracepath -n "${IPS[0]}" || true; else echo "tracepath not installed"; fi
} | tee "$OUT/environment.txt"

for ip in "${IPS[@]}"; do
  for version in tls1_2 tls1_3; do
    echo "===== $ip $version =====" | tee -a "$OUT/openssl.txt"
    timeout 14 openssl s_client -connect "$ip:443" -servername "$HOST" "-$version" -state -brief </dev/null 2>&1 \
      | tee -a "$OUT/openssl.txt" | tail -n 18
  done

  echo "===== curl pinned to $ip (preserve SNI/Host) =====" | tee -a "$OUT/curl-pinned.txt"
  curl -4 -v --resolve "$HOST:443:$ip" -o /dev/null \
    --connect-timeout 8 --max-time 20 \
    -w 'http_code=%{http_code} connect=%{time_connect}s tls=%{time_appconnect}s total=%{time_total}s remote_ip=%{remote_ip}\\n' \
    "https://$HOST/OpenAPI_File/files/OpenAPIScripMaster.json" \
    >>"$OUT/curl-pinned.txt" 2>&1 || true
  tail -n 12 "$OUT/curl-pinned.txt"

  echo "===== packet evidence $ip ====="
  if command -v tcpdump >/dev/null 2>&1; then
    PCAP="$OUT/$ip.pcap"
    timeout 18 tcpdump -ni any -s 0 -w "$PCAP" "host $ip and tcp port 443" >/dev/null 2>&1 &
    CAP_PID=$!
    sleep 1
    timeout 12 openssl s_client -connect "$ip:443" -servername "$HOST" -tls1_2 -brief </dev/null >/dev/null 2>&1 || true
    wait "$CAP_PID" 2>/dev/null || true
    tcpdump -nn -tttt -r "$PCAP" 2>/dev/null | tail -n 30 | tee -a "$OUT/packets.txt"
    rm -f "$PCAP"
  else
    echo "tcpdump not installed; packet capture skipped"
  fi
done

echo "===== primary endpoint HTTP test ====="
curl -4 -sS -o /dev/null -D "$OUT/headers.txt" \
  --connect-timeout 8 --max-time 25 \
  -w 'http_code=%{http_code} connect=%{time_connect}s tls=%{time_appconnect}s total=%{time_total}s remote_ip=%{remote_ip}\n' \
  "https://$HOST/OpenAPI_File/files/OpenAPIScripMaster.json" || true
cat "$OUT/headers.txt" 2>/dev/null || true

echo "Diagnostic files: $OUT"
echo "No server settings or services were changed."
