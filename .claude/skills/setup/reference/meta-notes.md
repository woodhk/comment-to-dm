# Meta notes

How the Meta part of setup actually went for a working bot, step by step, with the labels from a
read-only check of Meta's screens in November 2026. The chapters (2 to 5) follow this. Use this
file when a screen looks different, a step fails, or the bot reports a Meta error later.

Meta changes labels often. If a label differs, look for the nearest match and tell the creator
what you're clicking instead. If nothing matches, take a screenshot, search the web for the
current screen (see [stuck.md](stuck.md)), and explain what you found.

## The steps that worked

1. **Facebook login, not Instagram login.** Meta no longer offers "API setup with Instagram login"
   to new apps. The bot uses the Instagram API with Facebook login. It reaches Instagram through
   the linked Facebook Page, with a Page token that never expires, so there's nothing to refresh.
   `config.toml` has `api = "facebook_login"`, which is the default.
2. **The "Allow access to messages" switch is optional.** It often can't be found. Instagram may
   only show "who can send you messages" and "who can add to group chat", and DMs work anyway.
   Only look for it if DMs fail in the live test (chapter 2 lists where it has been).
3. **Use the Facebook account that's an admin of the Page.** The Page must be the one linked to
   the Instagram account. With Facebook login, the wrong account means the Page doesn't show up.
4. **VPN off.** With a VPN on, Facebook refused to save a change to the developer account.
   Switching the VPN off for Chrome cleared it about 9 minutes later. Keep it off from chapter 3
   to the end of chapter 5. Tailscale doesn't count. On Windows it has been on since chapter 13,
   and every server command needs it, so it stays on. Only a VPN app that changes their country
   goes off.
5. **Create the app.** developers.facebook.com/apps/, the green "Create App" button at the top
   right. The wizard's steps are App details, Use cases, Business, Requirements and Overview.
   - App details has "App name" (any name without Meta's brand words) and "App contact email"
     (filled in already).
   - Use cases opens on the "Featured" filter. "Manage messaging & content on Instagram" is under
     "All" (or "Content management").
   - On the Business step, choose "I don't want to connect a business portfolio yet".
   - Don't add "Get real-time notifications with Webhooks". The bot doesn't use webhooks.
   - The app list may show a banner about "New app lifecycle tools". Ignore it.
6. **Pick Facebook login.** In the app's left menu, "Use cases", then the Instagram row, then
   "Customize". It opens on "API setup with Unified login" with an orange "Business portfolio
   selection is mandatory" warning. That's expected. Unified login needs a business portfolio and a
   web server, so skip it and click "API setup with Facebook login". That tab says it "still works,
   but it's now legacy". Use it. The tabs are "Permissions and features", "API setup with Unified
   login", "API setup with Facebook login" and "Webhooks". Under "1. Add required permissions" are
   two groups, "Manage content on Instagram" and "Send messages on Instagram". Click both buttons.
   - "Add required content permissions" adds instagram_basic, instagram_content_publish,
     pages_read_engagement, business_management and pages_show_list. The list may show
     `instagram_content_publish` under the heading "instagram_content_publishing". It's the same
     permission.
   - "Add required messaging permissions" adds instagram_basic, instagram_manage_messages,
     pages_read_engagement, pages_show_list and business_management.
   - On an app that already has them, the buttons read "Go to permissions and features".
7. **Check the Page link.** business.facebook.com/latest/settings, then "Business assets", then
   "Pages", then the Page. A panel opens with the buttons "Assign people", "Assign partner" and
   "Connect assets", and the tabs "People", "Partners" and "Connected assets". The Instagram
   account must be listed on "Connected assets". If it's missing, "Connect assets" links it. The
   Meta Business Suite phone app can also make the link. The left menu also has "Instagram
   profiles", which is another place to look.
8. **Add the two the buttons leave out.** On the "Permissions and features" tab, click "Add" for
   `instagram_manage_comments` and for the feature "Business Asset User Profile Access" (the bot
   needs it for commenters' names). An added row shows "Ready for testing" with an "Actions" menu.
   Watch for lookalikes. Add `instagram_manage_comments`, not
   `instagram_business_manage_comments`, and the plain `instagram_basic`, not
   `instagram_business_basic`. The `instagram_business_` ones belong to Instagram login.
9. **Token in Graph API Explorer.** developers.facebook.com/tools/explorer/. In "Meta App" pick
   the app, in "User or Page" pick "User Token", then use "Add a Permission" for six permissions:
   instagram_basic, instagram_manage_comments, instagram_manage_messages, pages_show_list,
   pages_read_engagement and business_management. `pages_manage_metadata` "isn't offered for this
   app", so leave it out. The creator clicks the blue "Generate Access Token" and answers the
   dialogs. They choose "Opt in to current pages only" and tick their Page, then "Opt in to
   current businesses only", then their Instagram account. The token lasts about an hour, so go
   straight from making it to `ask_key.sh FB_USER_TOKEN` and `igdm connect`. The Explorer showed
   Graph API v26.0 in November 2026.
10. **App ID and secret.** developers.facebook.com/apps/<app id>/settings/basic/, which is "App
    settings", then "Basic", in the left menu. It shows "App ID", then "App secret" with a "Show"
    button. The creator clicks Show. Both go in with `ask_key.sh`, then `uv run igdm connect`.
    `uv run igdm check` comes later, once the reel is set. It first runs in chapter 7, then again
    in chapter 10.
11. **Messaging node.** `messaging_node = "page"` in `config.toml` worked first time. The other
    value, `"ig"`, was never needed.
12. **No App Review.** The app stayed "In development" and unpublished, with Standard Access, and
    DMs to ordinary followers with no role on the app worked. Each creator makes their own app, so
    each is its admin. The live test in chapter 11 confirms it for them.

## Errors and fixes

- **`code=190`, "The session has been invalidated because the user changed their password or
  Facebook has changed the session for security reasons".** The token stopped working, usually
  after a Facebook password change, or if the app or a permission was removed. Make a new token
  (step 9), put it in with `ask_key.sh FB_USER_TOKEN`, and run `igdm connect` again. If the bot
  was already running, restart the bot and the dashboard afterwards (see
  [../chapters/everyday.md](../chapters/everyday.md)). During chapter 5, a 190 usually just means
  the hour ran out.
- **`code=200`, "Cannot call API for app … on behalf of user", or "API access blocked".** The
  developer dashboard showed nothing about it, with no alert and "You don't currently have any
  required actions". The fix was to log in to facebook.com in the browser and pass the security
  check it showed.
- **`(#100) The Page is not linked to an Instagram account or the linked IG account is not a
  professional account`.** Go through chapter 2 (professional account) and chapter 3 (the Page
  link) again.
- **A comment that never shows up.** Instagram sometimes holds comments back from the API.
  Compare the reel's comment count on Instagram with what the API returns before blaming the bot.
- **Facebook refuses a change for no clear reason.** Check the VPN is off, then wait about ten
  minutes and try again.
- **A permission is missing from the token.** Check it's "Ready for testing" on the app's
  "Permissions and features" tab, with the right name (not an `instagram_business_` lookalike),
  then make a new token.

## Where secrets show on these pages

The App secret's "Show" button on the Basic page, and the "Access Token" box in Graph API
Explorer after "Generate Access Token". From the moment you ask them to click either one, follow
the hands-off rule in [chrome.md](chrome.md). The creator clicks Show and Generate Access Token,
never you.
