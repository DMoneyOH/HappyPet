## FUTURE REFERENCE: Memory Sharing Pattern (Claude.ai ↔ Claude Code)
<!-- DO NOT IMPLEMENT — noted for future use only -->

When ready to share memory between Claude.ai chat and Claude Code:
1. Add dedicated section to ~/.claude/memory/MEMORY.md:
   ## Claude.ai Chat Projects
   <!-- Managed by Claude.ai chat + Desktop Commander — never touch manually -->
   ### [ProjectName]
   - Status: [phase]
   - Key decisions: [list]
   - Last updated: [date]
2. Write ONLY to that section — never overwrite Claude Code's auto-written sections
3. Use append-only writes via Desktop Commander
4. Claude Code picks this up automatically on next session (autoMemory=true)
5. No API keys or passwords — MEMORY.md is plaintext
Pattern enables: zero handoff between environments, decisions persist across both

---

## FUTURE REFERENCE: Desktop Commander Persistence + Mobile Access

### Why DC Disconnects
- DC is conversation-scoped, not system-persistent
- Tied to active Claude.ai app window/tab
- Long context or idle sessions drop DC tool registry
- System staying on overnight doesn't help — app session must stay active

### Fix 1: Keep Claude.ai App Session Alive (Windows)
Add Windows Task Scheduler task to keep Claude app in foreground or ping it:
  - Task: every 30min, check Claude app is running
  - If not: relaunch via shortcut
  - Prevents idle timeout disconnecting DC

### Fix 2: DC Reconnection Pattern (already works)
When DC drops mid-conversation:
  tool_search("Desktop Commander start process write file")
  Then immediately read .session-context.md to restore project state

### Fix 3: Mobile Access Strategy
DC is Windows-only — cannot run on mobile directly.
Two options for mobile project work:
  A) Claude mobile app (claude.ai) — same account, same conversation history,
     but no DC access. Use for reading/planning only.
  B) Maeve Telegram bot (@ClaudePawbot) — has WSL access, can run scripts,
     check generator status, report results. Better for execution monitoring.
     Already set up and running on Derek-PC.

### Fix 4: Add DC Status Check to wsl-startup.sh
Add to ~/.claude/wsl-startup.sh:
  # Ensure Claude app is running (keeps DC available)
  powershell.exe -Command "Start-Process 'shell:AppsFolder\AnthropicPBC.Claude_pzs8sxrjxfjjc!App' -WindowStyle Minimized" 2>/dev/null || true
  echo "[$(date)] Claude app launched for DC availability" >> /tmp/wsl-startup.log

### Fix 5: Project State Always Recoverable
Regardless of DC drops, project state is always in:
  /home/derek/projects/pawpicks/.session-context.md  (human readable)
  /tmp/pawpicks_briefing.txt                          (last briefing output)
  /home/derek/.context-mode-claudeai/                (isolated ctx-mode DB)
First action on any reconnect: python3 /home/derek/projects/pawpicks/briefing.py

---

## FUTURE REFERENCE: Mobile ↔ Desktop Continuity (Full Solution)

### Why Native Mobile Access Doesn't Work
- DC is a Windows desktop extension — physically cannot run on mobile
- Claude.ai conversations are isolated sessions — mobile doesn't inherit desktop context
- Same account ≠ shared context window
- Even continuing same thread on mobile: DC tools don't load, no WSL access

### What Mobile CAN Do Today (No Extra Setup)
- Read desktop conversation history (scroll back)
- Continue same conversation thread for planning/strategy
- Ask questions that don't need DC or file access
- Review outputs I've already written to disk

### What Mobile CANNOT Do Today
- Trigger DC commands
- Run scripts on Derek-PC
- Check generator status
- Push to GitHub
- Access WSL filesystem

### Full Solution: Cloudflare Tunnel + Webhook (30 min setup, one-time)
Architecture:
  Mobile Claude.ai (any conversation)
      ↓ I call webhook URL
  Cloudflare Tunnel (free, outbound only, no port forwarding)
      ↓ HTTPS
  webhook_server.py on Derek-PC (Flask, systemd, always running)
      ↓ whitelisted commands only
  WSL filesystem + scripts
      ↓ results written to status file
  I read status file next message → report back

Components needed:
  1. cloudflared installed in WSL (free binary)
  2. Tunnel registered at dash.cloudflare.com (free account)
  3. webhook_server.py (~50 lines Flask, token-auth, whitelist only)
  4. systemd service for both cloudflared and webhook_server
  5. Add tunnel URL to session-context.md so I always know it

Security model:
  - Outbound only — no inbound ports opened on router
  - Shared secret token required on every request
  - Command whitelist — no arbitrary execution
  - Read-only endpoints for status, log tailing
  - Write endpoints only for pre-approved scripts (generator, git push)

Once built: works from mobile, desktop, any Claude session, any device.
Estimated cost: $0. Estimated setup time: 30 min (I do 25 of it).
