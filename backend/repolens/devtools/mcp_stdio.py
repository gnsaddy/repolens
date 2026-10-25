"""Let an editor that only speaks MCP over standard input/output use a RepoLens server.

    python -m repolens.devtools.mcp_stdio --url https://repolens.example.com/api/mcp   (token from REPOLENS_TOKEN)

Each line on standard input is a JSON-RPC message; it is sent to the server and the reply is written to standard output. The
access token is read from the environment so it never shows in a process list."""

import argparse
import json
import os
import sys

import httpx


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="the server's /api/mcp address")
    a = ap.parse_args()
    token = os.environ.get("REPOLENS_TOKEN", "")
    if not token:
        sys.exit("Set REPOLENS_TOKEN to a personal access token (Profile → Access tokens).")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
    with httpx.Client(timeout=120) as client:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                r = client.post(a.url, content=line, headers=headers)
            except httpx.HTTPError as e:
                msg = json.loads(line)
                if isinstance(msg, dict) and "id" in msg:
                    print(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {"code": -32000, "message": f"Cannot reach the server: {type(e).__name__}"},
                            }
                        ),
                        flush=True,
                    )
                continue
            if r.status_code == 202 or not r.content:
                continue
            if r.status_code == 401:
                msg = json.loads(line)
                if isinstance(msg, dict) and "id" in msg:
                    print(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {
                                    "code": -32001,
                                    "message": "The access token was refused. Create a new one in RepoLens.",
                                },
                            }
                        ),
                        flush=True,
                    )
                continue
            print(r.text.replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
