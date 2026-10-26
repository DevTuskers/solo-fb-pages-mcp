# Facebook (Meta) app setup

One-time setup by the **owner in a browser** (about 30 minutes). The agent guides them step by step, asks for
screenshots when a screen looks different, and collects values only through secure secret forms.
Below, `{{BASE_URL}}` is this server's URL; for a new deployment use that deployment's URL.

No App Review or business verification is needed: the owner and people with an app role use the app, and Standard
Access covers Pages they manage.

## 0. Prerequisites
- A personal Facebook account with **full control** of every Page to manage (Meta Business Suite > Settings > People).
  The tools need the Page tasks `CREATE_CONTENT` and `MODERATE` (`MANAGE` for insights).
- Register as a developer: https://developers.facebook.com > **Get Started** (verify phone/email if asked).

## 1. Create the app
1. Open https://developers.facebook.com/apps/creation/
2. **App details:** app name (e.g. `My Pages Agent`), contact email. **Next**.
3. **Use cases:** pick only **"Manage everything on your Page"**. Use cases cannot be removed later. **Next**.
4. **Business:** "I don't want to connect a business portfolio yet" (or pick your own). **Next**, then **Go to dashboard**.

## 2. Permissions
Dashboard > **Use cases** > "Manage everything on your Page" > **Customize** > **Permissions**. Click **Add** on:
`pages_manage_posts`, `pages_read_engagement`, `pages_read_user_content`, `pages_manage_engagement`,
`pages_manage_metadata`, `read_insights`, plus `publish_video` if it is listed (video posting works without it).
`pages_show_list`, `business_management` and `public_profile` are usually added automatically. "Page Mentions" is optional.

"Ready for testing" next to each permission is a **status**, not a button. In Development mode every added permission
already works for app-role users.

The left menu may be collapsed to icons: use the sidebar toggle at the bottom left, or hover the icons.

## 3. Facebook Login for Business: settings
Left menu: **Facebook Login for Business > Settings**.
1. **Client OAuth login:** ON. **Web OAuth login:** ON. **Enforce HTTPS:** ON. **Use Strict Mode for redirect URIs:** ON.
2. **Valid OAuth Redirect URIs:** `{{BASE_URL}}/oauth/callback` (press Enter so it becomes a chip).
3. **Deauthorize callback URL:** `{{BASE_URL}}/facebook/deauthorize`
4. **Data Deletion Request URL:** `{{BASE_URL}}/facebook/data-deletion`
5. **Save changes**.

## 4. Facebook Login for Business: configuration
Left menu: **Facebook Login for Business > Configurations** > **Create configuration** (or "Create from template > Create custom").
1. Name `pages-agent`. Login variation **General**.
2. Access token: **User access token**. Token expiration: **Never** if offered.
3. Assets: **Pages**.
4. Permissions: tick everything from step 2.
5. Create. Copy the **Configuration ID** (a long number): this is `FB_LOGIN_CONFIG_ID`.

## 5. App settings > Basic
Left menu: **App settings > Basic**.
- **Privacy Policy URL:** `{{BASE_URL}}/privacy`
- **User data deletion:** "Data deletion instructions URL" = `{{BASE_URL}}/facebook/data-deletion`
- **Category:** "Business and pages" (or "Business").
- **App icon:** optional in Development mode (512-1024 px square PNG if wanted).
- Namespace, App domains, Terms of Service URL: leave empty.
- **Save changes**.
- Copy **App ID** (`FB_APP_ID`, not secret) and click **Show** next to **App secret** (`FB_APP_SECRET`, secret; Facebook
  asks for the password).

## 6. Hand over the values
The agent sends secure secret forms for `FB_APP_ID`, `FB_APP_SECRET`, `FB_LOGIN_CONFIG_ID` (saved for the organization).
- `FB_APP_ID` and `FB_LOGIN_CONFIG_ID` are not secret (they appear in Facebook's login URL). The agent sets them on the
  server: `fly secrets set -a <app> FB_APP_ID=... FB_LOGIN_CONFIG_ID=...`.
- `FB_APP_SECRET` **never goes to the server** (it refuses to start if it is set). It stays in the agent's secret store
  and is sent with every request (see `devin-setup` > "How credentials work").

Check `{{BASE_URL}}/healthz` shows `"facebook_configured": true`.

## 7. Connect Pages
Agent calls `create_onboarding_link` and sends the URL (15 min, single use). The owner opens it in the browser where
they are logged in to Facebook, clicks **Connect with Facebook**, selects the Pages (all, or a subset), keeps all
permissions, then on the server's page ticks the Pages to save and optionally sets aliases (e.g. `test` for the
throwaway Page). Repeat any time to add or reconnect Pages.

## 8. Require App Secret (after the server works)
**App settings > Advanced > Security > Require App Secret: ON**, then **Save changes**. The server sends
`appsecret_proof` on every call. Verify with any read tool (e.g. `get_page_info`).

## 9. App roles (optional)
Co-managers need an app role: **App roles > Roles > Add People** (Developer or Tester); they must accept the invite.

## 10. Development vs Live mode
Development mode is fine for an owner-only setup: the owner and app-role users can do everything. Switching to
**Live** (**Publish** in the left menu) is only needed if non-role users must use the app or if content the app
publishes is not publicly visible: check one post in a logged-out/incognito window. If the dashboard demands App Review
for going Live, stop and tell the owner.

## Revoke everything
Facebook **Settings & privacy > Settings > Business Integrations** > remove the app (kills all tokens). Reset the App
Secret under **App settings > Basic** if it leaked.
