# Troubleshooting

## Connecting to the server
| Symptom | Cause / fix |
|---|---|
| `/mcp` returns 401 | Wrong or revoked key. Check the client's secret value; get a new key with `create_client_key` (`devin-setup`). |
| Devin MCP "secret unresolved" | The secret named in the MCP header does not exist in that org, or the session started before it was saved. Save it at **organization** scope and start a new session. |
| `/healthz` shows `facebook_configured: false` | `FB_APP_ID`, `FB_APP_SECRET` or `FB_LOGIN_CONFIG_ID` missing on the server (`fly secrets list`). |
| Onboarding/claim link says expired | Links last 15 minutes and work once. Create a new one. |

## Facebook errors (returned as `{"error": {"code": ..., "message": ..., "user_message"?, "hint"?}}`)
| Code | Meaning | What to do |
|---|---|---|
| 190 | Token invalid/expired; Page marked `needs_reconnect` | `create_onboarding_link`, owner reconnects the Page |
| 10, 200-299 | Missing permission or Page task | Check `check_page_connection` scopes; owner needs full control of the Page; reconnect after adding permissions |
| 200 "Can not hide or unhide this comment" | The comment was written by the Page itself | Cannot be hidden; delete it instead if wanted |
| 100 | Invalid parameter or unsupported object | Check IDs (post IDs look like `<pageid>_<postid>`) and arguments |
| 100 "API calls from the server require an appsecret_proof" | Require App Secret is ON but the server's `FB_APP_SECRET` is wrong | Fix the Fly secret |
| 1, 2 | Facebook-side unknown/temporary error | Retry once later; report if persistent |
| 4, 17, 32, 613, 80001 | Rate limited | The server already retries with backoff; slow down |
| 368 | Action blocked as abusive/spam | Stop and tell the owner; do not retry |

## Known Graph quirks (handled by the tools)
- Page-authored posts reject `is_hidden`; hiding uses `timeline_visibility=hidden` and unhiding `forced_allow`.
- Only the **Like** reaction can be added or removed.
- Video and reel uploads finish asynchronously: poll `get_video_status` until the status is `ready` (or `error`).
