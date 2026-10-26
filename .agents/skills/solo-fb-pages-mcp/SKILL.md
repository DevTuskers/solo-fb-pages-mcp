---
name: solo-fb-pages-mcp
description: Set up, connect to, deploy or use the solo-fb-pages-mcp Facebook Pages MCP server (Meta app setup, Fly.io deploy, connecting a new Devin organization, posting/moderating Pages, troubleshooting).
---

# solo-fb-pages-mcp

The authoritative guides are markdown files in `src/solo_fb_pages_mcp/guides/`. The running server also serves them
through the `get_guide` tool and `guide://<topic>` MCP resources (with the real server URL filled in).

| Job | Guide |
|---|---|
| Orientation | `index.md` |
| Post, moderate, read insights with the tools | `using.md` |
| Connect another Devin organization / MCP client to the existing server | `devin-setup.md` |
| Create or fix the Meta (Facebook) app | `facebook-setup.md` |
| Deploy a new copy on Fly.io, day-2 ops, kill switch | `fly-deploy.md` |
| Errors and Graph quirks | `troubleshooting.md` |

Always:
- Run commands on your own VM; the owner only clicks in a browser and fills secure secret forms.
- Never put secrets in chat, git or PR text.
- Never write to a real Page unless the owner asked; experiment on their test Page and clean up.
- Deploy only when the owner asks: `fly deploy --remote-only --ha=false -a solo-fb-pages-mcp`.
- Checks before committing: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy && uv run pytest -q`.
