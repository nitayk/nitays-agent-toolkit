#!/bin/bash
# Isolated test of the agent-mailbox resource lease. Uses its own root; never touches ~/.agent-mailbox.
set -u
ROOT="${TMPDIR:-/tmp}/agent-mailbox-lease-test"
mv "$ROOT" "$ROOT.old-$$" 2>/dev/null
export AGENT_MAILBOX_ROOT="$ROOT"
M="python3 $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mailbox.py"

pass=0; fail=0
chk() { # chk <label> <expected-exit> <actual-exit>
  if [ "$2" = "$3" ]; then echo "  PASS  $1"; pass=$((pass+1))
  else echo "  FAIL  $1 (expected exit $2, got $3)"; fail=$((fail+1)); fi
}

AGENT_MAILBOX_SESSION=sessA $M register --name alpha --focus "repo A" >/dev/null 2>&1
AGENT_MAILBOX_SESSION=sessB $M register --name beta  --focus "repo B" >/dev/null 2>&1

echo "-- acquire + conflict --"
AGENT_MAILBOX_SESSION=sessA $M lease --resource my-repo --note "payments PR" >/dev/null 2>&1
chk "alpha acquires a free repo" 0 $?
AGENT_MAILBOX_SESSION=sessB $M lease --resource my-repo >/dev/null 2>&1
chk "beta blocked on the same repo" 1 $?
AGENT_MAILBOX_SESSION=sessB $M lease --resource my-repo/services/payments >/dev/null 2>&1
chk "beta blocked on a CHILD path (parent covers child)" 1 $?
AGENT_MAILBOX_SESSION=sessB $M lease --resource my-repo-other >/dev/null 2>&1
chk "beta allowed on a LOOKALIKE name (not a child)" 0 $?
AGENT_MAILBOX_SESSION=sessA $M lease --resource "My-Repo " >/dev/null 2>&1
chk "alpha renews its own lease (case/space normalized)" 0 $?

echo "-- child-then-parent (reverse direction) --"
AGENT_MAILBOX_SESSION=sessA $M lease --resource "build#74" >/dev/null 2>&1
AGENT_MAILBOX_SESSION=sessB $M lease --resource "build" >/dev/null 2>&1
chk "beta blocked taking a PARENT of a held child" 1 $?

echo "-- who holds what --"
HOLDS=$(AGENT_MAILBOX_SESSION=sessB $M peers 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(';'.join(f\"{p['name']}:{','.join(p['holds'])}\" for p in d['peers']))")
echo "  holds -> $HOLDS"
case "$HOLDS" in *"alpha:build#74,my-repo"*) chk "peers shows alpha's holds" 0 0;; *) chk "peers shows alpha's holds" 0 1;; esac

echo "-- release --"
AGENT_MAILBOX_SESSION=sessB $M release --resource my-repo >/dev/null 2>&1
STILL=$($M leases 2>/dev/null | python3 -c "import json,sys;print(sum(1 for l in json.load(sys.stdin)['leases'] if l['resource']=='my-repo'))")
chk "beta CANNOT release alpha's lease (still held)" 1 "$STILL"
AGENT_MAILBOX_SESSION=sessA $M release --resource my-repo >/dev/null 2>&1
GONE=$($M leases 2>/dev/null | python3 -c "import json,sys;print(sum(1 for l in json.load(sys.stdin)['leases'] if l['resource']=='my-repo'))")
chk "alpha releases its own lease" 0 "$GONE"
AGENT_MAILBOX_SESSION=sessB $M lease --resource my-repo >/dev/null 2>&1
chk "beta can take it once released" 0 $?

echo "-- expiry (lease, not lock) --"
AGENT_MAILBOX_SESSION=sessA $M lease --resource short-lived --ttl-h 0.0001 >/dev/null 2>&1
sleep 1
AGENT_MAILBOX_SESSION=sessB $M lease --resource short-lived >/dev/null 2>&1
chk "an EXPIRED lease does not block a peer" 0 $?

echo "-- register surfaces occupied lanes --"
N=$(AGENT_MAILBOX_SESSION=sessC $M register --name gamma 2>/dev/null | python3 -c "import json,sys;print(len(json.load(sys.stdin)['peer_leases']))")
[ "$N" -ge 2 ] && chk "a NEW session sees peer leases on register ($N)" 0 0 || chk "a NEW session sees peer leases on register ($N)" 0 1

echo "-- concurrency: 12 parallel racers, exactly one must win --"
for i in $(seq 1 12); do
  ( AGENT_MAILBOX_SESSION="race$i" $M register --name "racer$i" >/dev/null 2>&1
    AGENT_MAILBOX_SESSION="race$i" $M lease --resource contested-repo >/dev/null 2>&1
    echo $? >> "$ROOT/.race-results" ) &
done
wait
WINS=$(grep -c '^0$' "$ROOT/.race-results" 2>/dev/null || echo 0)
chk "exactly ONE racer won (got $WINS)" 1 "$WINS"

echo "-- empty resource rejected --"
AGENT_MAILBOX_SESSION=sessA $M lease --resource "   " >/dev/null 2>&1
[ $? -ne 0 ] && chk "blank resource rejected" 0 0 || chk "blank resource rejected" 0 1

echo
echo "RESULT: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
