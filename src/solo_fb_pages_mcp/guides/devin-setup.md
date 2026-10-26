# Connect a new Devin organization (or any MCP client) to this server

Use this when the server at `{{BASE_URL}}` is already running with Pages connected, and an agent in **another** Devin
organization (or another MCP client) should manage the same Pages. Nothing changes on the Facebook side: Pages, tokens
and the Meta app stay as they are. Each organization gets **its own key**, so it can be revoked on its own and the
audit log shows which organization made each change.

## What the new agent asks the owner for

1. A one-time **key link** for this organization (step 1).
2. To paste the key into a **secure secret form** (step 2). Never into chat.
3. To **approve** the MCP install (step 3), or to add it by hand.
4. Which Page is the **test Page**, and approval before writing to any real Page.
5. Only if this organization must also deploy or change the server: a Fly login approval (`fly-deploy`) and GitHub
   access to the repo (step 5).

## Step 1: get a key link

Choose a short client name for the organization, e.g. `devtuskers` (a-z, 0-9, `-`, `_`).

- **Option A (usual):** in a session of an organization that already has this MCP, call
  `create_client_key(name="devtuskers")`. It returns `claim_url` (valid 15 minutes, single use).
- **Option B (no MCP access anywhere, but Fly access):** from an agent VM logged in to Fly:
  `fly ssh console -a solo-fb-pages-mcp -C "solo-fb-pages-mcp --create-key-link devtuskers"`

Send `claim_url` to the owner. Do not open it yourself.

## Step 2: owner reveals the key and stores it as a secret

The new organization's agent first sends a secure secret request named **`MCP_SOLO_FB_PAGES_API_KEY`**, saved at
**organization** scope (Devin MCP installs require the secret name to start with `MCP_`). Then the owner:

1. Opens `claim_url` and clicks **Reveal key**, then **Copy**. The key is shown once; revealing replaces any older key
   for the same client name.
2. Pastes it into the secret form and chooses to save it for the organization.

Without an agent, the owner can add it manually: Devin **Settings > Secrets** (https://app.devin.ai/settings/secrets),
add secret, name `MCP_SOLO_FB_PAGES_API_KEY`, scope **Organization**.

## Step 3: add the MCP server to the organization

**Agent route:** use the MCP-install tool (Devin: `devin_mcp_server_manage`, action `install`) with:
- name `Solo FB Pages`, slug `solo-fb-pages`, short description "Manage the owner's Facebook Pages"
- transport `HTTP`, url `{{BASE_URL}}/mcp`
- auth method `auth_header`, header `Authorization`, format `bearer`, secret name `MCP_SOLO_FB_PAGES_API_KEY`
- installation scope `org`

The owner approves the card that appears.

**Manual route (owner, in the new organization):**
1. Open **Customize > MCPs** (https://app.devin.ai/customize?tab=mcps), then **Add MCP > Add custom MCP**.
2. Server Name `Solo FB Pages`, Short Description "Manage my Facebook Pages".
3. Transport **HTTP**, Server URL `{{BASE_URL}}/mcp`.
4. Authentication method **Auth Header**: header key `Authorization`, value `Bearer ${MCP_SOLO_FB_PAGES_API_KEY}`
   (or `Bearer <the key>` pasted directly).
5. Scope **Organization**. **Save**, then **Test tools**. It should list 36 tools (`list_pages`, `create_post`, ...).

## Step 4: verify from a new session

MCP servers and secrets load when a session starts, so start a **new** session in that organization, then:
1. `list_pages`: the same Pages and aliases as everywhere else.
2. `get_guide(topic="using")`: read the rules.
3. `get_audit_log(limit=5)`: your writes will show `client: "devtuskers"`.

## Step 5 (optional): code and deploy access

- **Repo:** the code is public, so any agent can `git clone https://github.com/aspriya/solo-fb-pages-mcp` and read it
  without a GitHub connection. Opening PRs needs a Devin GitHub connection that covers the repo. Per Devin's docs, one
  GitHub account or organization can be connected to only one Devin account at a time, so it is simplest to keep code
  changes in the organization that already has that GitHub connection.
- **Deploys:** see `fly-deploy` > "Getting Fly access on a new agent VM". Deploy only when the owner asks.

## Removing access
`list_client_keys` shows each client's last use. `revoke_client_key(name, confirm=true)` cuts that client off at once.
Rotating the primary `MCP_API_KEY` (Fly secret) cuts off the `owner` client only; named keys keep working.
