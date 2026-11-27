#!/bin/bash
set -u
cd "$(dirname "$0")"
python3 proxy.py 47101 /tmp/proxy.log & PROXY=$!
python3 -c 'import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(("127.0.0.1",47102));s.listen(5);time.sleep(600)' & OTHER=$!
sleep 1
PROFILE='(version 1)
(allow default)
(deny network-outbound)
(deny network-bind (local ip))
(deny network-inbound (local ip))
(allow network-outbound (remote unix-socket))
(deny network-outbound (remote unix-socket (path-literal "/private/var/run/mDNSResponder")))
(allow network-outbound (remote ip "localhost:47101"))'
sw_vers
echo "== baseline"; python3 probe.py 47101 47102
echo "== sandboxed"; /usr/bin/sandbox-exec -p "$PROFILE" python3 probe.py 47101 47102; echo "rc=$?"
echo "== curl direct"; /usr/bin/sandbox-exec -p "$PROFILE" curl -sS -m 10 -o /dev/null -w '%{http_code}\n' https://example.com; echo "rc=$?"
echo "== curl via proxy"; /usr/bin/sandbox-exec -p "$PROFILE" env HTTPS_PROXY=http://127.0.0.1:47101 curl -sS -m 20 -o /dev/null -w '%{http_code}\n' https://example.com; echo "rc=$?"
echo "== git via proxy"; /usr/bin/sandbox-exec -p "$PROFILE" env HTTPS_PROXY=http://127.0.0.1:47101 git ls-remote https://github.com/git/git HEAD; echo "rc=$?"
echo "== nslookup"; /usr/bin/sandbox-exec -p "$PROFILE" nslookup example.com 1.1.1.1; echo "rc=$?"
echo "== dscacheutil"; /usr/bin/sandbox-exec -p "$PROFILE" dscacheutil -q host -a name example.com; echo "rc=$?"
echo "== ping"; /usr/bin/sandbox-exec -p "$PROFILE" ping -c1 -t3 1.1.1.1; echo "rc=$?"
echo "== nested sandbox-exec"; /usr/bin/sandbox-exec -p "$PROFILE" /usr/bin/sandbox-exec -p '(version 1)(allow default)' /bin/echo nested-ok; echo "rc=$?"
echo "== child inherits"; /usr/bin/sandbox-exec -p "$PROFILE" bash -c 'bash -c "curl -sS -m 8 -o /dev/null https://example.com"'; echo "rc=$?"
echo "== node"; /usr/bin/sandbox-exec -p "$PROFILE" node -e 'require("https").get("https://example.com",r=>console.log("node direct",r.statusCode)).on("error",e=>console.log("node direct blocked",e.code))'
echo "== npm i claude-code"; npm i -g @anthropic-ai/claude-code >/dev/null 2>&1 && /usr/bin/sandbox-exec -p "$PROFILE" claude --version; echo "rc=$?"
echo "== claude -p via proxy (no login)"; /usr/bin/sandbox-exec -p "$PROFILE" env HTTPS_PROXY=http://127.0.0.1:47101 perl -e 'alarm 60; exec @ARGV' claude -p hi 2>&1 | tail -3
echo "== proxy log"; cat /tmp/proxy.log
kill $PROXY $OTHER
