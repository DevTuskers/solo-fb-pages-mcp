# Deploy a new copy on Fly.io (from an agent VM)

Use this only for a **new, separate** deployment. To use the existing server at `{{BASE_URL}}`, read `devin-setup`.
The owner never runs commands; the agent runs everything on its VM.

**The server holds no Facebook secrets.** It refuses to start if `FB_APP_SECRET`, `TOKEN_ENCRYPTION_KEY` or
`ADMIN_PASSWORD` are set. The App Secret and the token key live in each agent's own secret store and travel with
every request (see `devin-setup` > "How credentials work"). Only non-secret config is on Fly: `FB_APP_ID`,
`FB_LOGIN_CONFIG_ID`, `PUBLIC_BASE_URL`, and optionally `MCP_API_KEY` (client `owner`).

Cost: one `shared-cpu-1x` 512 MB Machine + a 1 GB volume (a few USD/month). Do not buy or upgrade anything else.

## Getting Fly access on a new agent VM
1. `curl -L https://fly.io/install.sh | sh` and `export PATH="$HOME/.fly/bin:$PATH"`.
2. Run `fly auth login` in a long-running shell. It prints a `https://fly.io/app/auth/cli/...` URL and waits.
3. Send the URL to the owner (Fly account with a payment method). They approve; if a one-time code is shown, collect
   it through a secure secret form and enter it. Logins expire after about 15 minutes; start a fresh one if needed.
4. `fly auth whoami`. Optionally create an app-scoped token `fly tokens create deploy -a <app> --expiry 2160h` and save
   it as an agent secret (`FLY_API_TOKEN`) through a save prompt. Never print it.

## First deploy
```bash
git clone https://github.com/aspriya/solo-fb-pages-mcp && cd solo-fb-pages-mcp
APP=solo-fb-pages-mcp            # if taken, pick another and edit `app` and PUBLIC_BASE_URL in fly.toml
fly apps create "$APP" -o personal
fly volumes create data --size 1 -r sin -a "$APP" -y      # region nearest the owner
fly deploy --remote-only --ha=false -a "$APP"             # remote builder: no local Docker
curl -s https://$APP.fly.dev/healthz
# token key: generate on the VM, never print, save to the agent's secret store (e.g. Devin secret FB_TOKEN_KEY)
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())" > ~/.token_key
# first client key (no MCP_API_KEY needed): owner reveals it, agent appends ~<FB_APP_SECRET>~<TOKEN_KEY>
fly ssh console -a "$APP" -C "solo-fb-pages-mcp --create-key-link <org-name>"
```
Keep exactly **one Machine** (SQLite lives on the volume). Losing the token key means reconnecting every Page.

Then: `facebook-setup` (with `https://$APP.fly.dev`), set `FB_APP_ID` and `FB_LOGIN_CONFIG_ID` on Fly, save
`FB_APP_SECRET` only in the agent's secret store, add the MCP (`devin-setup` step 3), connect Pages with
`create_onboarding_link`.

## Day-2 operations
| Task | Command |
|---|---|
| Deploy current code (only when the owner asks) | `fly deploy --remote-only --ha=false -a <app>` |
| Status / logs | `fly status -a <app>`, `fly logs -a <app>` |
| Config / secret names | `fly secrets list -a <app>` (must not list FB_APP_SECRET / TOKEN_ENCRYPTION_KEY / ADMIN_PASSWORD) |
| Key link for a new client | `create_client_key(name)` from a client, or `fly ssh console -a <app> -C "solo-fb-pages-mcp --create-key-link <name>"` |
| Read-only kill switch | set `READ_ONLY = "true"` in `fly.toml` `[env]` and deploy |
| Volume snapshots | `fly volumes snapshots list <volume-id>` (14 days kept) |

### Rotate the token key
Generate a new key on the VM, then, without printing either key:
`fly ssh console -a <app> -C "env OLD_TOKEN_KEY=<old> NEW_TOKEN_KEY=<new> solo-fb-pages-mcp --rewrap"`.
Then update every client's secret (`<key>~<FB_APP_SECRET>~<new>`), e.g. via fresh `create_client_key` links.

There is no automatic deploy from GitHub; deploys are run by an agent on request.

## Kill switch
1. Facebook: remove the app under Settings & privacy > Business Integrations (revokes all Page tokens).
2. `revoke_client_key` for every client (and `fly secrets unset MCP_API_KEY` if set).
3. Reset the App Secret in the Meta dashboard (every client's secret then needs the new one).
4. `fly tokens list` / `fly tokens revoke <id>`.
