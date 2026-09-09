#!/usr/bin/env bash
# UserPromptSubmit hook: surface this session's unread agent-mailbox count.
# Silent (and near-free) for sessions that never registered a mailbox.
# The mailbox root only exists once someone has registered — skip the python
# interpreter spawn entirely for the (majority) consumers who never opt in.
[ -d "${AGENT_MAILBOX_ROOT:-$HOME/.agent-mailbox}" ] || exit 0
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$SKILL_DIR/scripts/mailbox.py" notify 2>/dev/null || true
