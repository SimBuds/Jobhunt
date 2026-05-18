# macOS Mac mini AI Orchestration Stack

### Hermes Agent + OpenClaw (Telegram/Discord bridge) + Claude API — May 2026

A memory-conscious AI agent stack for an **M4 Mac mini, 16 GB unified memory,
512 GB SSD** (Apple's current entry SKU at $799 since the May 2026 retirement
of the 256 GB tier). **Hermes Agent** (Nous Research) is the self-improving
agent with a built-in learning loop. **OpenClaw** (Peter Steinberger,
`steipete`; formerly *Moltbot* / *Clawdbot* before the Nov 2025 rebrand) is
optionally bridged in as a **Telegram + Discord** inbound surface for Hermes.
All heavy inference is offloaded to the **Claude API**; nothing runs locally.

A small **FastAPI host gateway** brokers AppleScript / iMessage calls so the
containerized agent never touches macOS automation APIs directly. iMessage
intentionally stays on this native path rather than going through OpenClaw —
that keeps OpenClaw's surface narrow (just Telegram + Discord) and leaves
real headroom on a 16 GB box.

> **Relationship note.** Hermes and OpenClaw are *peer* open-source projects,
> not parent/child. Hermes already has first-class sandboxed terminal
> backends (local, Docker, SSH, Modal, Daytona, Vercel Sandbox, Singularity).
> OpenClaw here is purely an inbound channel adapter — Hermes is still the
> agent doing the work. You do not need OpenClaw at all if iMessage is
> enough.
>
> **CLI churn warning.** Both projects move quickly. Re-verify exact command
> names against upstream docs the day you install:
> - Hermes: <https://hermes-agent.nousresearch.com/docs>
> - OpenClaw: <https://docs.openclaw.ai>

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
   Docker terminal backend          OpenClaw gateway (optional,
   (Hermes' sandboxed tool exec)    Telegram + Discord only)
              │
              ▼
   FastAPI capability server (host, 127.0.0.1:9000)
              │
              ▼
   Native macOS APIs (AppleScript, Mail, Finder, Calendar, iMessage)
```

---

## 1. System requirements & memory strategy

### Hardware (this guide is tuned for)
- **Mac mini, Apple Silicon M4, 16 GB unified memory, 512 GB SSD.** Apple
  skipped the M3 generation for Mac mini, so M1 / M2 / M4 are the valid
  options today. An M5 Mac mini is rumored for mid-to-late 2026 but not yet
  shipping. As of May 2026 Apple discontinued the 256 GB tier — 512 GB is
  the new base SKU and `$799` the new entry price.
- **Reserve ≥ 80 GB free** at all times. A 512 GB SSD has ~466 GB usable
  after macOS; the 64 GB Docker disk image + image cache + Hermes skill DB
  + browser cache will land around 90–120 GB over a few months. Below 80 GB
  free, macOS starts thrashing swap.

### The golden rule
**Zero local LLM inference.** Ollama, vLLM, MLX, llama.cpp on 16 GB next to
this stack = swap thrash. All inference goes to the Claude API.

### Realistic memory budget on 16 GB

| Component | Resident RAM |
| --- | --- |
| macOS + window server | 4–6 GB |
| Docker Desktop overhead | ~1.5 GB |
| Postgres + Redis containers | ~400 MB |
| Hermes container (idle → active) | 1.0–1.75 GB |
| OpenClaw gateway + 1 Chromium context | 0.9–1.2 GB |
| Browser / editor / Messages.app | 2–4 GB |
| **Committed before headroom** | **~10–14 GB** |

This fits 16 GB but leaves little room for Slack, Zoom, or a second IDE
running in parallel. Treat it as the upper bound of comfortable use.

---

## 2. macOS permissions

System Settings → Privacy & Security → enable these for your terminal
emulator (Terminal, iTerm2, Ghostty, etc.):

- **Accessibility** — required for AppleScript / window control.
- **Automation** — granted on first prompt per target app (Messages, Mail,
  Calendar).
- **Screen Recording** — only if you plan to use vision-based browser tooling.
- **Full Disk Access** — only if your agent needs to read protected user
  folders.

---

## 3. Install core dependencies

```bash
# Homebrew (Apple Silicon path)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
source ~/.zprofile

# Core CLI tooling. Do NOT brew install postgresql or redis — they run in
# containers. Do NOT brew install docker/docker-compose — Docker Desktop
# ships its own CLI and `docker compose` (V2) plugin.
brew install git wget jq tmux htop neovim node@24

# uv for Python env management
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zprofile

# Verify Node — OpenClaw needs ≥ 22.14; 24 LTS is recommended.
node --version   # v24.x preferred; v22.14+ also works
```

---

## 4. Install & configure Docker Desktop

Download Docker Desktop for Mac (Apple Silicon) from <https://docker.com>.

In Docker Desktop → Settings:

| Section | Setting |
| --- | --- |
| **General** | Enable VirtioFS, Enable Use Virtualization Framework, Enable Rosetta (only if x86 images are needed) |
| **Resources → Advanced → CPUs** | 4 cores |
| **Resources → Advanced → Memory** | 4.0 GB |
| **Resources → Advanced → Swap** | 1.0 GB |
| **Resources → Advanced → Disk image size** | 64 GB |

The 4 GB / 1 GB allocation is deliberate. 6–8 GB to Docker on a 16 GB Mac
starves macOS once you also have a browser, Messages, and an IDE open. Bump
later only if you actually run out of container RAM in practice.

Verify Compose V2 is active:

```bash
docker compose version    # should report v2.x
```

> All compose commands below use `docker compose` (space). The hyphenated
> `docker-compose` is legacy V1.

---

## 5. Project structure

```bash
mkdir -p ~/ai-stack/{capability-server,shared/{postgres,redis,workspace},logs,secrets}
mkdir -p ~/.hermes        # Hermes persists config + skill DB here
cd ~/ai-stack
```

```text
~/ai-stack/
├── capability-server/   # FastAPI host gateway (runs on the Mac, not in Docker)
├── shared/
│   ├── postgres/        # Persistent Postgres data
│   ├── redis/           # Persistent Redis data
│   └── workspace/       # Files visible to Hermes' Docker terminal
├── logs/
└── secrets/             # API keys (chmod 600)

~/.hermes/               # Hermes state — config.yaml, skills, conversation history
~/.openclaw/             # OpenClaw config + workspace (only if you install it)
```

---

## 6. API secrets

The Postgres password is the one piece of secret material that does not
already exist somewhere. Generate it without echoing it to your shell history:

```bash
# Generate, copy to clipboard, paste at the prompt below.
openssl rand -base64 24 | pbcopy
printf 'ANTHROPIC_API_KEY=sk-ant-your-real-key-here\n' >  ~/ai-stack/secrets/anthropic.env
printf 'POSTGRES_USER=hermes\n'                        >> ~/ai-stack/secrets/anthropic.env
printf 'POSTGRES_DB=hermes\n'                          >> ~/ai-stack/secrets/anthropic.env
read -rsp 'Paste Postgres password: ' pw && printf 'POSTGRES_PASSWORD=%s\n' "$pw" >> ~/ai-stack/secrets/anthropic.env && unset pw
chmod 600 ~/ai-stack/secrets/anthropic.env
```

Then open the file once with `nvim ~/ai-stack/secrets/anthropic.env` and
replace the `ANTHROPIC_API_KEY` placeholder with your real key.

---

## 7. Docker Compose stack

Create `~/ai-stack/docker-compose.yml`:

```yaml
# No top-level `version:` key — Compose V2 deprecates it.

services:
  postgres:
    image: postgres:18-alpine
    restart: unless-stopped
    env_file:
      - ./secrets/anthropic.env
    volumes:
      - ./shared/postgres:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    mem_limit: 384m

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    volumes:
      - ./shared/redis:/data
    ports:
      - "127.0.0.1:6379:6379"
    mem_limit: 192m

  hermes:
    # Official Hermes image. Confirm the current tag on the Hermes docs
    # before pulling — the image namespace has moved before.
    image: nousresearch/hermes-agent:latest
    restart: unless-stopped
    env_file:
      - ./secrets/anthropic.env
    environment:
      # Hermes also reads ~/.hermes/config.yaml (mounted below). Env vars
      # only cover the secret + the DB/Redis URLs.
      HERMES_DATABASE_URL: postgresql://hermes:${POSTGRES_PASSWORD}@postgres:5432/hermes
      HERMES_REDIS_URL: redis://redis:6379/0
      CAPABILITY_SERVER_URL: http://host.docker.internal:9000
    volumes:
      - ~/.hermes:/root/.hermes           # config + skill DB persist on the host
      - ./shared/workspace:/workspace
      - ./logs:/logs
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      - postgres
      - redis
    extra_hosts:
      - "host.docker.internal:host-gateway"
    mem_limit: 1750m
```

All ports bind to `127.0.0.1` so nothing on your LAN can reach the stack.

### Configure Hermes to use Claude

Hermes is configured through `~/.hermes/config.yaml` (and equivalent CLI
commands), **not** through `HERMES_PROVIDER`-style env vars. The container
mount above means anything you put here is read inside the container:

```bash
mkdir -p ~/.hermes
cat > ~/.hermes/config.yaml <<'EOF'
inference:
  provider: anthropic
  model:
    default: claude-opus-4-7
EOF
```

The container reads `ANTHROPIC_API_KEY` from the env file. If you prefer the
CLI form, after the stack is up you can run:

```bash
docker compose exec hermes hermes inference set anthropic
docker compose exec hermes hermes model set claude-opus-4-7
```

---

## 8. Host capability server (FastAPI)

The capability server runs **natively** on the Mac so that AppleScript calls
execute under your user account, not inside a container.

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
    # Loopback-only — never expose this to the network.
    uvicorn.run(app, host="127.0.0.1", port=9000)
```

Two notes on the script form:

- `shlex.quote()` is for **POSIX shells**, not AppleScript. The dedicated
  AppleScript escape above handles backslashes and double quotes.
- Both `participant … of targetService` and `buddy "…"` work on current
  macOS. `participant` is preferred when addressing by raw handle (phone or
  email); `buddy` is fine for entries that already exist in Messages'
  conversation list.

Run the server:

```bash
python capability_server.py
```

For a permanent install behind `launchd`, see **Appendix A**.

---

## 9. (Optional) OpenClaw as a Telegram + Discord bridge

If you want to talk to your agent from Telegram or Discord, install OpenClaw
**on the host** (not in Docker). OpenClaw runs `node`-native and ships its
own gateway daemon; running it inside Docker would just add overhead.

```bash
# Requires Node ≥ 22.14; Node 24 LTS recommended.
# Install pnpm if you don't have it (OpenClaw docs prefer pnpm; npm/bun also work).
brew install pnpm
pnpm add -g openclaw@latest
openclaw onboard --install-daemon
```

`openclaw onboard --install-daemon` walks you through:

1. **Gateway daemon install** — registered with `launchd` so it survives
   reboots.
2. **Model provider** — pick Anthropic. When prompted for the API key,
   paste the same value you wrote to `~/ai-stack/secrets/anthropic.env`.
   OpenClaw persists it in `~/.openclaw/openclaw.json` — it does not read
   that env file directly.
3. **Channel pairing** — select **only Telegram and Discord**. Skip every
   other channel; each extra channel adds resident memory and adapter
   processes. iMessage stays on the FastAPI server we already set up.
4. **Workspace** — defaults to `~/.openclaw/workspace`.

### Pin OpenClaw to a single browser context and the two channels

Edit `~/.openclaw/openclaw.json`:

```json
{
  "agent": {
    "model": "anthropic/claude-opus-4-7"
  },
  "channels": {
    "enabled": ["telegram", "discord"]
  },
  "browser": {
    "maxConcurrentBrowsers": 1,
    "maxPagesPerContext": 1,
    "viewport": { "width": 1280, "height": 720 }
  }
}
```

### Channel setup

- **Telegram** — create a bot via [@BotFather](https://t.me/BotFather), paste
  the bot token when OpenClaw asks. Per-channel guidance:
  <https://docs.openclaw.ai/channels/telegram>.
- **Discord** — create an application + bot at
  <https://discord.com/developers/applications>, copy the bot token, invite
  the bot to your server with the message-content intent.

OpenClaw and Hermes speak ACP. In this setup OpenClaw is the inbound
transport (Telegram + Discord messages → Hermes); Hermes does the work and
replies through OpenClaw.

---

## 10. Launch the stack

```bash
cd ~/ai-stack
docker compose up -d
docker compose ps              # confirm running
docker compose logs -f hermes
```

In a separate pane, monitor memory:

```bash
htop
# Watch the "Swp" column (macOS calls it "Swap" in Activity Monitor).
# Sustained Swap > 12 GB means close a browser session or stop OpenClaw.
```

---

## 11. Operational practices

### Daily restart

Find your Docker path (Docker Desktop on Apple Silicon usually symlinks to
`/usr/local/bin/docker`, but newer installs put the real binary under
`/Applications/Docker.app/Contents/Resources/bin/docker`):

```bash
which docker
```

Then add to `crontab -e`, substituting the path you got:

```text
0 3 * * * cd $HOME/ai-stack && /usr/local/bin/docker compose restart >> $HOME/ai-stack/logs/restart.log 2>&1
```

### Monthly cleanup

```bash
docker builder prune -af
docker image prune -af
docker volume prune -f       # won't touch named volumes currently in use
```

### Keep ≥ 80 GB free
macOS swap is dynamic. Run out of disk and you effectively run out of RAM.

---

## 12. What this 16 GB build can and can't run

**Validated:**
- Hermes Agent with the Anthropic provider, planning loop, persistent memory.
- **One** Chromium context with **one** page via Hermes' Docker terminal
  backend (matches OpenClaw's `maxConcurrentBrowsers: 1`).
- Native macOS triggers via the capability server (iMessage, Mail, Calendar,
  Finder).
- OpenClaw as a Telegram + Discord bridge alongside Hermes.

**Don't try:**
- Local model inference (Ollama, vLLM, MLX, llama.cpp, Whisper-large).
- Multiple simultaneous browser contexts / tab fanout.
- Self-hosted vector DBs at meaningful scale (single-node Qdrant/Weaviate is
  OK for a few hundred thousand vectors; a real cluster is not).
- Hermes **and** OpenClaw **and** a heavy IDE **and** a browser comfortably
  at the same time.

If any of those are non-negotiable, the next sensible step is a 24 GB M4 Pro
mini or a 32 GB+ machine.

---

## Appendix A. `launchd` plist for the capability server

Drop this at `~/Library/LaunchAgents/com.local.capability-server.plist` (edit
the `python` path to match your `uv venv`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.local.capability-server</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USER/ai-stack/capability-server/.venv/bin/python</string>
        <string>/Users/YOUR_USER/ai-stack/capability-server/capability_server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USER/ai-stack/logs/capability-server.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USER/ai-stack/logs/capability-server.err</string>
</dict>
</plist>
```

Load it:

```bash
launchctl load ~/Library/LaunchAgents/com.local.capability-server.plist
```

---

## Appendix B. Upstream references

CLI flags and config keys in both projects have changed multiple times in
the last six months. Treat this guide's commands as a starting point and
diff against upstream the day you install:

- Hermes Agent docs: <https://hermes-agent.nousresearch.com/docs>
- Hermes Agent repo: <https://github.com/NousResearch/hermes-agent>
- OpenClaw docs: <https://docs.openclaw.ai>
- OpenClaw repo: <https://github.com/openclaw/openclaw>
