# Connect a new Devin organization (or any MCP client) to this server

Use this when the server at `{{BASE_URL}}` is already running with Pages connected, and an agent in **another** Devin
organization (or another MCP client) should manage the same Pages. Nothing changes on the Facebook side.

## How credentials work (read this first)
The server stores **no Facebook secrets**. Page tokens in its database are encrypted with a key it never keeps.
Every MCP request must carry three values:

| Value | What it is |
|---|---|
| access key | This client's own MCP key (one per organization, revocable, shows up in the audit log) |
| `FB_APP_SECRET` | The Meta app's App Secret |
| `TOKEN_KEY` | The Fernet key that decrypts the stored Page tokens (shared by every client) |

Devin (one header only) sends them as one secret value: `Authorization: Bearer <access key>~<FB_APP_SECRET>~<TOKEN_KEY>`.
Other clients may instead send `Authorization: Bearer <access key>` plus headers `X-FB-App-Secret` and
`X-SFPM-Token-Key`, and keep the values in their own secret manager (AWS Secrets Manager, Vault, ...).
Without the Facebook values, only local tools work (`list_pages`, `get_audit_log`, `get_guide`, key management).

## What the new agent asks the owner for
1. A one-time **key link** for this organization, created from an organization that already has the MCP (step 1).
2. To paste the revealed value into a **secure secret form** (step 2). Never into chat.
3. To **approve** the MCP install (step 3), or to add it by hand.
4. Which Page is the **test Page**, and approval before writing to any real Page.
5. Only if this organization must also deploy or change the server: a Fly login approval (`fly-deploy`).

## Step 1: get a key link
Choose a short client name, e.g. `devtuskers` (a-z, 0-9, `-`, `_`).

- **Usual:** in a session of an organization that already has this MCP, call `create_client_key(name="devtuskers")`.
  Because that request carries the Facebook credentials, the revealed value is already the complete
  `<key>~<FB_APP_SECRET>~<TOKEN_KEY>` string. Check `includes_facebook_credentials: true` in the result.
- **No MCP access anywhere, but Fly access:**
  `fly ssh console -a solo-fb-pages-mcp -C "solo-fb-pages-mcp --create-key-link devtuskers"`.
  This reveals only the access key; the agent must append `~<FB_APP_SECRET>~<TOKEN_KEY>` from the owner's secret store.

`claim_url` is valid 15 minutes, single use, and dies if the server restarts. Send it to the owner; never open it yourself.

## Step 2: owner reveals the value and stores it as a secret
The new organization's agent first sends a secure secret request named **`MCP_SOLO_FB_PAGES_API_KEY`**, saved at
**organization** scope (Devin MCP installs require the name to start with `MCP_`). Then the owner opens `claim_url`,
clicks **Reveal key**, **Copy**, and pastes the value into that form. It is shown once; revealing replaces any older
key for the same client name.

Manual alternative: Devin **Settings > Secrets** (https://app.devin.ai/settings/secrets), add secret
`MCP_SOLO_FB_PAGES_API_KEY`, scope **Organization**.

## Step 3: add the MCP server to the organization
**Agent route:** use the MCP-install tool (Devin: `devin_mcp_server_manage`, action `install`) with name `Solo FB Pages`,
slug `solo-fb-pages`, transport `HTTP`, url `{{BASE_URL}}/mcp`, auth method `auth_header`, header `Authorization`,
format `bearer`, secret `MCP_SOLO_FB_PAGES_API_KEY`, scope `org`. The owner approves the card.

**Manual route:** **Customize > MCPs** (https://app.devin.ai/customize?tab=mcps) > **Add MCP > Add custom MCP**:
Server Name `Solo FB Pages`; transport **HTTP**; URL `{{BASE_URL}}/mcp`; authentication **Auth Header**, key
`Authorization`, value `Bearer ${MCP_SOLO_FB_PAGES_API_KEY}`; scope **Organization**. **Save**, then **Test tools**
(36 tools).

## Step 4: verify from a new session
MCP servers and secrets load when a session starts, so start a **new** session, then call `list_pages`,
`get_page_info(page="test")` (proves the Facebook credentials work), `get_guide(topic="using")` and
`get_audit_log(limit=5)` (your calls show your client name).

## Step 5 (optional): code and deploy access
- **Repo:** public at https://github.com/aspriya/solo-fb-pages-mcp; an organization may also keep its own copy.
- **Deploys:** see `fly-deploy`. Deploy only when the owner asks.

## Rotating or removing access
- `list_client_keys` shows each client's last use; `revoke_client_key(name, confirm=true)` cuts one client off.
- Rotating `TOKEN_KEY` or the App Secret means updating every client's secret (`fly-deploy` > "Rotate the token key").
