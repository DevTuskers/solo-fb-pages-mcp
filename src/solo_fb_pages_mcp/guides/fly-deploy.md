# Deploy a new copy on Fly.io (from an agent VM)

Use this only for a **new, separate** deployment (e.g. another owner). To use the existing server at `{{BASE_URL}}`,
read `devin-setup` instead. The owner never runs commands; the agent runs everything on its VM.

Cost: one `shared-cpu-1x` 512 MB Machine + a 1 GB volume (a few USD/month). Do not buy or upgrade anything else.

## Getting Fly access on a new agent VM
1. Install flyctl: `curl -L https://fly.io/install.sh | sh` and `export PATH="$HOME/.fly/bin:$PATH"`.
2. Run `fly auth login` in a long-running shell. It prints a `https://fly.io/app/auth/cli/...` URL and waits.
3. Send the URL to the owner (they must have a Fly account with a payment method). They open it and approve. If the
   page shows a one-time code, ask for it through a secure secret form and enter it in the waiting prompt.
   The login expires after about 15 minutes: start a fresh one if it does.
4. `fly auth whoami` confirms. For later sessions, optionally create an app-scoped token
   `fly tokens create deploy -a <app> --name devin-<org> --expiry 2160h` and save it as an org secret (`FLY_API_TOKEN`)
   through a secret-save prompt. Never print it.

## First deploy
```bash
git clone https://github.com/aspriya/solo-fb-pages-mcp && cd solo-fb-pages-mcp
APP=solo-fb-pages-mcp                     # pick another name if taken; then edit `app` and PUBLIC_BASE_URL in fly.toml
fly apps create "$APP" -o personal
fly volumes create data --size 1 -r sin -a "$APP" -y   # region: nearest to the owner (sin = Singapore)
# generate secrets on the VM, never print them
MCP_API_KEY=$(openssl rand -hex 32)
ADMIN_PASSWORD=$(openssl rand -base64 24)
TOKEN_ENCRYPTION_KEY=$(python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
fly secrets set --stage -a "$APP" MCP_API_KEY="$MCP_API_KEY" ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  TOKEN_ENCRYPTION_KEY="$TOKEN_ENCRYPTION_KEY"
fly deploy --remote-only --ha=false -a "$APP"   # remote builder: no local Docker needed
curl -s https://$APP.fly.dev/healthz
```
Keep exactly **one Machine** (SQLite lives on the volume). Save `MCP_API_KEY`, `ADMIN_PASSWORD` and
`TOKEN_ENCRYPTION_KEY` to the agent's secret store via save prompts; tell the owner to keep `TOKEN_ENCRYPTION_KEY`
safe too (losing it means reconnecting every Page).

Then: `facebook-setup` (uses `https://$APP.fly.dev`), set the three FB secrets, connect Pages, add the MCP to Devin
(`devin-setup` step 3, using `MCP_API_KEY` as the key or a key from `--create-key-link`).

## Day-2 operations
| Task | Command |
|---|---|
| Deploy current code (only when the owner asks) | `fly deploy --remote-only --ha=false -a <app>` |
| Status / logs | `fly status -a <app>`, `fly logs -a <app>` |
| List secret names | `fly secrets list -a <app>` |
| Change a secret | `fly secrets set -a <app> NAME=...` (restarts the Machine) |
| Shell / one-off command | `fly ssh console -a <app>` / `fly ssh console -a <app> -C "<cmd>"` |
| Key link for a new client | `fly ssh console -a <app> -C "solo-fb-pages-mcp --create-key-link <name>"` |
| Read-only kill switch | set `READ_ONLY = "true"` in `fly.toml` `[env]` and deploy |
| Volume snapshots | `fly volumes snapshots list <volume-id>` (14 days kept) |

There is no automatic deploy from GitHub; deploys are run by an agent on request.

## Kill switch
1. Facebook: remove the app under Settings & privacy > Business Integrations (revokes all Page tokens).
2. `fly secrets set -a <app> MCP_API_KEY=<new>` and `revoke_client_key` for every named client.
3. Reset the App Secret in the Meta dashboard.
4. `fly tokens list` / `fly tokens revoke <id>`.
