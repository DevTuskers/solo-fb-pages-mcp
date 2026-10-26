# Troubleshooting

## Connecting to the server
| Symptom | Cause / fix |
|---|---|
| `/mcp` returns 401 `unauthorized` | Wrong or revoked access key. Get a new one with `create_client_key` (`devin-setup`). |
| `/mcp` returns 401 `malformed credentials` | The bearer value is not `key` or `key~app_secret~token_key`, or the token key is not a Fernet key. |
| Tool error "no Facebook credentials" | The client sends only its access key. Store `<key>~<FB_APP_SECRET>~<TOKEN_KEY>` as the secret (`devin-setup`). |
| Tool error "could not be decrypted" | The client's `TOKEN_KEY` is wrong or outdated (key rotated). Get the current one from the owner's secret store. |
| Server logs "Refusing to start" | `FB_APP_SECRET`, `TOKEN_ENCRYPTION_KEY` or `ADMIN_PASSWORD` is set on Fly. `fly secrets unset` them. |
| Devin MCP "secret unresolved" | The secret named in the MCP header does not exist in that org, or the session started before it was saved. Save it at **organization** scope and start a new session. |
| `/healthz` shows `facebook_configured: false` | `FB_APP_ID`, `FB_APP_SECRET` or `FB_LOGIN_CONFIG_ID` missing on the server (`fly secrets list`). |
| Onboarding/claim link says expired | Links last 15 minutes, work once, and die on a server restart (credentials are only in memory). Create a new one. |

## Facebook errors (returned as `{"error": {"code": ..., "message": ..., "user_message"?, "hint"?}}`)
| Code | Meaning | What to do |
|---|---|---|
| 190 | Token invalid/expired; Page marked `needs_reconnect` | `create_onboarding_link`, owner reconnects the Page |
| 10, 200-299 | Missing permission or Page task | Check `check_page_connection` scopes; owner needs full control of the Page; reconnect after adding permissions |
| 200 "Can not hide or unhide this comment" | The comment was written by the Page itself | Cannot be hidden; delete it instead if wanted |
| 100 | Invalid parameter or unsupported object | Check IDs (post IDs look like `<pageid>_<postid>`) and arguments |
| 100 "API calls from the server require an appsecret_proof" / "Invalid appsecret_proof" | The client's `FB_APP_SECRET` is missing or wrong | Fix the client's secret value |
| 1, 2 | Facebook-side unknown/temporary error | Retry once later; report if persistent |
| 4, 17, 32, 613, 80001 | Rate limited | The server already retries with backoff; slow down |
| 368 | Action blocked as abusive/spam | Stop and tell the owner; do not retry |

## Known Graph quirks (handled by the tools)
- Page-authored posts reject `is_hidden`; hiding uses `timeline_visibility=hidden` and unhiding `forced_allow`.
- Only the **Like** reaction can be added or removed.
- Video and reel uploads finish asynchronously: poll `get_video_status` until the status is `ready` (or `error`).
