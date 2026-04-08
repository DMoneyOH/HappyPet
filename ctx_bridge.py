#!/usr/bin/env python3
"""
ctx_bridge.py — Context-mode MCP bridge for Claude Desktop
Spawns context-mode as a subprocess, speaks JSON-RPC over stdio,
exposes simple CLI for Claude Desktop to call via Desktop Commander.

Usage:
  python3 ctx_bridge.py execute "<code>" [--lang bash|python] [--intent "description"]
  python3 ctx_bridge.py search "<query>"
  python3 ctx_bridge.py stats
  python3 ctx_bridge.py tools
  python3 ctx_bridge.py file "<path>"
"""

import subprocess, json, sys, os, threading, queue, time, argparse

PATH = "/home/derek/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
ENV  = {**os.environ, "PATH": PATH,
        "CONTEXT_MODE_NO_HOOKS": "1",
        "CONTEXT_MODE_NO_INSTALL": "1"}
CTX  = ["npx", "-y", "context-mode"]

class ContextBridge:
    def __init__(self):
        self.proc = subprocess.Popen(
            CTX, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=ENV, text=True, bufsize=1
        )
        self._id  = 0
        self._q   = queue.Queue()
        self._t   = threading.Thread(target=self._read, daemon=True)
        self._t.start()
        self._init()

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try: self._q.put(json.loads(line))
                except: pass

    def _send(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc":"2.0","id":self._id,"method":method,"params":params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return self._id

    def _recv(self, rid, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self._q.get(timeout=1)
                if msg.get("id") == rid:
                    if "error" in msg: raise RuntimeError(msg["error"])
                    return msg.get("result", {})
            except queue.Empty: continue
        raise TimeoutError(f"No response for id={rid}")

    def _init(self):
        rid = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "claude-desktop-bridge", "version": "1.0"}
        })
        self._recv(rid, timeout=20)
        self._send("notifications/initialized")

    def tools(self):
        rid = self._send("tools/list")
        r = self._recv(rid)
        return [t["name"] for t in r.get("tools", [])]

    def call(self, name, **kwargs):
        rid = self._send("tools/call", {"name": name, "arguments": kwargs})
        r   = self._recv(rid, timeout=90)
        return "\n".join(c.get("text","") for c in r.get("content",[]) if c.get("type")=="text")

    def execute(self, code, lang="bash", intent=""):
        return self.call("ctx_execute", code=code, language=lang, intent=intent or code[:60])

    def search(self, query):
        return self.call("ctx_search", query=query)

    def file(self, path):
        return self.call("ctx_execute_file", file_path=path)

    def stats(self):
        """Get context compression stats."""
        try: return self.call("ctx_stats")
        except: return self.execute("echo 'Context-mode active'", intent="stats check")

    def close(self):
        try: self.proc.terminate()
        except: pass


def main():
    ap = argparse.ArgumentParser(description="Context-mode bridge for Claude Desktop")
    ap.add_argument("action", choices=["execute","search","stats","tools","file"])
    ap.add_argument("arg", nargs="?", default="")
    ap.add_argument("--lang", default="shell")
    ap.add_argument("--intent", default="")
    args = ap.parse_args()

    bridge = ContextBridge()
    try:
        if args.action == "tools":
            tools = bridge.tools()
            print(f"Available tools ({len(tools)}):")
            for t in tools: print(f"  {t}")

        elif args.action == "execute":
            if not args.arg:
                print("ERROR: provide code to execute", file=sys.stderr); sys.exit(1)
            result = bridge.execute(args.arg, lang=args.lang, intent=args.intent)
            print(result)

        elif args.action == "search":
            if not args.arg:
                print("ERROR: provide search query", file=sys.stderr); sys.exit(1)
            result = bridge.search(args.arg)
            print(result)

        elif args.action == "file":
            if not args.arg:
                print("ERROR: provide file path", file=sys.stderr); sys.exit(1)
            result = bridge.file(args.arg)
            print(result)

        elif args.action == "stats":
            result = bridge.stats()
            print(result)

    finally:
        bridge.close()

if __name__ == "__main__":
    main()
