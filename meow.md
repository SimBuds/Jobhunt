# macOS Mac mini AI Orchestration Stack

### Hermes Agent + OpenClaw + Claude API (16 GB Memory-Conscious Build) — May 2026

This guide builds a memory-conscious AI agent stack on a 16 GB Apple Silicon Mac mini. **Hermes Agent** (Nous Research) is the self-improving agent with a closed learning loop. **OpenClaw** (Peter Steinberger / OpenClaw Foundation) is optionally bridged in for multi-channel messaging (Telegram, Discord, iMessage, WhatsApp, etc.). All heavy inference is offloaded to the **Claude API**; nothing runs locally.

A small **FastAPI host gateway** brokers AppleScript / iMessage calls so containerized agents never touch the macOS automation APIs directly.

> **Important relationship note.** Hermes and OpenClaw are *peer* open-source agent frameworks, not parent/child. Hermes already has first-class sandboxed terminal backends (Docker, SSH, Modal, Daytona, Vercel Sandbox, Singularity, local). This guide uses Hermes' built-in **Docker terminal backend** for sandboxed tool execution and treats OpenClaw as an optional messaging bridge via the Agent Communication Protocol (ACP). You do not need OpenClaw to make Hermes work.

---

## Architecture

```text
                    Claude API (cloud inference)
                              │
                              ▼
                  Hermes Agent (planning + skills)
                              │
              ┌───────────────┼────────────────┐
              ▼                                ▼
   Docker terminal backend          OpenClaw gateway (optional)
   (Hermes' sandboxed tool exec)    iMessage / Telegram / Slack /
                                    Discord / WhatsApp / Signal
              │
              ▼
   FastAPI capability server (host, 127.0.0.1:9000)
              │
              ▼
   Native macOS APIs (AppleScript, Mail, Finder, Calendar)
```

---

## 1. System requirements & memory strategy

### Hardware
- **Mac mini, Apple Silicon, 16 GB unified memory.** Note: Apple skipped the M3 generation for Mac mini, so valid options are **M1, M2, or M4**. An M5 mini is rumored for 2026 but not yet shipping at the time of writing.
- **At least 150 GB free SSD.** macOS leans hard on swap when Chromium and containers spike.

### The golden rule
**Zero local LLM inference.** Running Ollama, vLLM, MLX, or any local model alongside this stack on a 16 GB machine will swap-thrash. Inference goes to the Claude API only.

### Realistic memory budget on 16 GB
Containers cap at ~4.75 GB combined. Add Docker Desktop's own overhead (~1–2 GB), macOS itself (~4–6 GB), and a browser/editor, and you are at the ceiling. Expect noticeable swap pressure if you also run Slack, Zoom, or a second IDE. This is the *upper bound* of what 16 GB can support, not a comfortable cruising altitude.

---

## 2. macOS permissions

System Settings → Privacy & Security → enable these for your terminal emulator (Terminal, iTerm2, Ghostty, etc.):

- **Accessibility** — required for AppleScript / window control.
- **Automation** — granted on first prompt per target app (Messages, Mail, Calendar).
- **Screen Recording** — only if you plan to use vision-based browser tooling.
- **Full Disk Access** — only if your agent needs to read protected user folders.

---

## 3. Install core dependencies

```bash
# Homebrew (Apple Silicon path)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
source ~/.zprofile

# Core CLI tooling. Note: do NOT brew install postgresql or redis here —
# they run in containers. Do NOT brew install docker / docker-compose —
# Docker Desktop ships its own CLI and `docker compose` (V2) plugin.
brew install git wget jq tmux htop neovim node@24

# uv for Python env management
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zprofile

# Verify Node 24 (OpenClaw requirement)
node --version   # should be v24.x
```

---

## 4. Install & configure Docker Desktop

Download Docker Desktop for Mac (Apple Silicon) from docker.com.

In Docker Desktop → Settings:

| Section | Setting |
| --- | --- |
| **General** | Enable VirtioFS, Enable Use Virtualization Framework, Enable Rosetta (only if x86 images needed) |
| **Resources → Advanced → CPUs** | 4 cores |
| **Resources → Advanced → Memory** | 6.0–8.0 GB |
| **Resources → Advanced → Swap** | 2.0 GB |
| **Resources → Advanced → Disk image size** | 64 GB |

Verify Compose V2 is the active plugin:

```bash
docker compose version    # should report v2.x
```

> All compose commands below use `docker compose` (space). The hyphenated `docker-compose` is legacy V1 and is not what Docker Desktop installs.

---

## 5. Project structure

```bash
mkdir -p ~/ai-stack/{hermes,capability-server,shared/{postgres,redis,workspace},logs,secrets}
cd ~/ai-stack
```

```text
~/ai-stack/
├── hermes/              # Hermes Agent config (Dockerfile or compose-only)
├── capability-server/   # FastAPI host gateway
├── shared/
│   ├── postgres/        # Persistent DB volume
│   ├── redis/           # Persistent cache volume
│   └── workspace/       # Files visible to Hermes' Docker terminal
├── logs/
└── secrets/             # API keys (chmod 600)
```

OpenClaw, if used, installs separately as a global npm package on the host — its workspace lives at `~/.openclaw/workspace` and its config at `~/.openclaw/openclaw.json` (not under `~/ai-stack/`).

---

## 6. API secrets

```bash
cat > ~/ai-stack/secrets/anthropic.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-your-real-key-here
POSTGRES_USER=hermes
POSTGRES_PASSWORD=replace-me-with-a-strong-random-value
POSTGRES_DB=hermes
EOF

chmod 600 ~/ai-stack/secrets/anthropic.env
```

Use `openssl rand -base64 24` to generate a real password.

---

## 7. Docker Compose stack

Create `~/ai-stack/docker-compose.yml`:

```yaml
# No top-level `version:` key — it is obsolete in Compose V2 and will
# produce a deprecation warning if present.

services:
  postgres:
    image: postgres:17-alpine
    restart: unless-stopped
    env_file:
      - ./secrets/anthropic.env
    volumes:
      - ./shared/postgres:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    mem_limit: 512m

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - ./shared/redis:/data
    ports:
      - "127.0.0.1:6379:6379"
    mem_limit: 256m

  hermes:
    build: ./hermes
    restart: unless-stopped
    env_file:
      - ./secrets/anthropic.env
    environment:
      # Tell Hermes to use Anthropic transport + Claude as primary
      HERMES_PROVIDER: anthropic
      HERMES_MODEL: claude-opus-4-7
      HERMES_DATABASE_URL: postgresql://hermes:${POSTGRES_PASSWORD}@postgres:5432/hermes
      HERMES_REDIS_URL: redis://redis:6379/0
      # Capability server runs on the host, reachable via the Docker host gateway
      CAPABILITY_SERVER_URL: http://host.docker.internal:9000
    volumes:
      - ./shared/workspace:/workspace
      - ./logs:/logs
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      - postgres
      - redis
    extra_hosts:
      - "host.docker.internal:host-gateway"
    mem_limit: 2g
```

Bind ports to `127.0.0.1` rather than `0.0.0.0` so the services are not reachable from your local network.

### Hermes Dockerfile

Create `~/ai-stack/hermes/Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install Hermes Agent from upstream
RUN pip install --no-cache-dir hermes-agent

WORKDIR /app
EXPOSE 8080

# Hermes reads its config from /app/hermes.toml or env vars
CMD ["hermes", "serve", "--host", "0.0.0.0", "--port", "8080"]
```

> Check the Hermes Agent repo (`github.com/NousResearch/hermes-agent`) for the current CLI flags and the recommended Python version before building. The package name and entrypoint can shift between versions.

---

## 8. Host capability server (FastAPI)

The capability server runs **natively** on the Mac so that AppleScript calls execute under your user account, not inside a container.

```bash
cd ~/ai-stack/capability-server
uv venv
source .venv/bin/activate
uv pip install fastapi uvicorn pydantic
```

Create `~/ai-stack/capability-server/capability_server.py`:

```python
import re
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="macOS Native Gateway", version="2026.05")


def applescript_escape(s: str) -> str:
    """Escape a Python string for safe use inside an AppleScript double-quoted literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


PHONE_OR_EMAIL = re.compile(r"^[\w.+\-@]+$|^\+?[\d\s\-()]+$")


class MessageRequest(BaseModel):
    recipient: str = Field(..., max_length=128, description="Phone number or iMessage email")
    message: str = Field(..., max_length=4000)


@app.post("/send-imessage")
def send_imessage(data: MessageRequest):
    if not PHONE_OR_EMAIL.match(data.recipient):
        raise HTTPException(400, "recipient must be a phone number or email")

    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{applescript_escape(data.recipient)}" of targetService
        send "{applescript_escape(data.message)}" to targetBuddy
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
    )

    if result.returncode != 0:
        raise HTTPException(500, result.stderr.strip())

    return {"status": "sent", "recipient": data.recipient}


if __name__ == "__main__":
    import uvicorn
    # Loopback-only — never expose this to the network
    uvicorn.run(app, host="127.0.0.1", port=9000)
```

Two corrections versus typical guides you'll see online:

- `shlex.quote()` is for **POSIX shells**, not AppleScript. We use a dedicated AppleScript escape that handles both backslashes and double quotes.
- The `participant … of targetService` form is the modern way to address an iMessage recipient by handle. The older `buddy "phone"` form only works for contacts already in your buddy list.

Run the server (and consider putting it behind `launchd` so it survives reboots — see the agent's own docs for `launchd` templates):

```bash
python capability_server.py
```

---

## 9. (Optional) OpenClaw for multi-channel messaging

If you want to talk to your agent over iMessage, Telegram, Discord, WhatsApp, Slack, Signal, etc., install OpenClaw **on the host** (not in Docker):

```bash
# Requires Node 24
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

`openclaw onboard` walks you through:
- gateway daemon install (launchd on macOS),
- model provider (point it at Anthropic with the same `ANTHROPIC_API_KEY` from `secrets/anthropic.env`),
- channel pairing,
- workspace at `~/.openclaw/workspace` and config at `~/.openclaw/openclaw.json`.

To restrict OpenClaw to a single browser context for memory safety, edit `~/.openclaw/openclaw.json`:

```json
{
  "agent": {
    "model": "anthropic/claude-opus-4-7"
  },
  "browser": {
    "maxConcurrentBrowsers": 1,
    "maxPagesPerContext": 1,
    "viewport": { "width": 1280, "height": 720 }
  }
}
```

OpenClaw and Hermes can coexist; they speak ACP, but they do not require each other. Pick one as your primary surface and use the other for what it does best.

---

## 10. Launch the stack

```bash
cd ~/ai-stack
docker compose up -d
docker compose ps          # confirm running
docker compose logs -f hermes
```

In a separate pane, monitor memory:

```bash
htop
# Watch the "Swp" line — sustained swap > 12 GB means clear browser sessions
# or stop OpenClaw and rerun.
```

---

## 11. Operational practices

### Daily restart
Add to `crontab -e`:

```text
0 3 * * * cd $HOME/ai-stack && /usr/local/bin/docker compose restart >> $HOME/ai-stack/logs/restart.log 2>&1
```

Adjust the docker path with `which docker`. On Apple Silicon with Docker Desktop, this is typically `/usr/local/bin/docker` (Docker Desktop creates a symlink).

### Monthly cleanup

```bash
docker builder prune -af
docker image prune -af
docker volume prune -f       # be careful — won't touch named volumes in use
```

### Keep at least 150 GB free
macOS swap is dynamic. Run out of disk and you run out of RAM too.

---

## 12. What this 16 GB build can and can't run

**Validated:**
- Hermes Agent with Anthropic transport, planning loop, persistent memory.
- Single-tab Playwright / Chromium via Hermes' Docker terminal backend.
- Native macOS triggers via the capability server (iMessage, Mail, Calendar, Finder).
- OpenClaw as a multi-channel gateway alongside Hermes.

**Don't try:**
- Local model inference (Ollama, vLLM, MLX, llama.cpp, Whisper-large).
- Multiple simultaneous browser contexts / tab fanout.
- Self-hosted vector DBs at meaningful scale (Qdrant/Weaviate single-node ≤ a few hundred thousand vectors is OK; a real cluster is not).
- Running Hermes *and* OpenClaw *and* a heavy IDE *and* a browser comfortably at the same time on 16 GB.

If you need any of those, the next sensible step is a 24 GB M4 Pro mini or a 32 GB+ machine.
