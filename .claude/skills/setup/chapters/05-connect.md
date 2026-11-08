# Chapter 5: Connect

## Why this matters

This is where the bot meets their Instagram for the first time. It needs three things from Meta.

- The App ID is the number that names their Meta app. It isn't a secret.
- The app secret is the app's private password. Only the bot may know it.
- A token is a long password that lets the bot act for their Page. The one from Graph API
  Explorer (Meta's page for trying things out) only lasts about an hour.

Then `igdm connect` swaps that short token for a Page token that never expires, and saves it in
their keys file. After this, there's nothing to refresh.

## Steps

Do these one at a time. Keep the VPN off, but on Windows leave Tailscale on, because the
secrets go to the server through it. The token goes last, because it's the one that runs
out, and from the moment it's made you go straight to the window and `igdm connect` without
stopping.

All three values go in through the ask_key window, the App ID included. The App ID isn't a secret,
and you may read it off the page to tell them which number it is. It still goes through the same
window, because `igdm set-key --stdin` is only for values you made yourself. One path is simpler
for them, too.

1. **Say what's coming.** Tell them in a few sentences what the three values are (from "Why this
   matters"), that each one goes into a private window on their computer and never into the chat,
   and that the last part has to be done in one go.

2. **The App ID.** Open https://developers.facebook.com/apps/, click their app, then in the left
   menu click "App settings", then "Basic". The page shows "App ID" and, under it, "App secret"
   with a "Show" button. The secret stays hidden until someone clicks Show, so you can read this
   page now.
   - Point them to the App ID at the top of the page. They copy it.
   - On a Mac, this is the first private window of the setup, so warn them first that macOS may
     ask whether Claude can control Terminal, as in
     [../reference/who-does-what.md](../reference/who-does-what.md).
   - Run `bash scripts/ask_key.sh META_APP_ID` and say the words from who-does-what.md about the
     window.
   - When they say it's saved, run `uv run igdm keys`. `META_APP_ID` should say `set`.

3. **The app secret.** This one is a real secret, so the hands-off rule starts here (see
   [../reference/chrome.md](../reference/chrome.md)).
   - Tell them where it is. It's on the same page, under App ID, with a "Show" button on the right.
     Clicking Show is theirs, and Facebook may ask for their password first.
   - Run `bash scripts/ask_key.sh META_APP_SECRET`, then tell them to click Show, copy the secret
     and paste it into the window.
   - From now until they say it's pasted, make no page reads, finds or screenshots of that tab.
     Tab titles are fine.
   - When they say it's saved, ask them to reload the page so the secret is hidden again, or to
     leave the page. Only then look at the tab again.
   - Run `uv run igdm keys`. `META_APP_SECRET` should say `set`.
   - Write a note, `"05-connect": "App ID and app secret set, token next"`.

4. **Get ready for the token.** Tell them this part has to go in one go:

   > The next part makes the token, and it only lasts about an hour. So once it's made, we go
   > straight to the window and connect the bot. It only takes a few minutes, but please don't
   > take a break halfway.

5. **Set up Graph API Explorer.** If Chrome asks again whether you can use the site, ask them to
   click Allow. You do the setting up here, because nothing new is secret until the token is made.
   - Graph API Explorer often opens with a token already in the "Access Token" box, left from an
     earlier visit. After a retry it's the token made a few minutes ago, and that one still works
     for about an hour. So the tab is hands off from the moment it opens (see
     [../reference/chrome.md](../reference/chrome.md)). This holds every time you come to this
     step, including a retry.
     1. Open https://developers.facebook.com/tools/explorer/, and don't read, find or screenshot
        anything on it yet.
     2. Ask them to click in the "Access Token" box near the top, select everything in it, and
        delete it. Then ask them to tell you whether the box is empty.
     3. Only when they say it's empty may you look at the page. If they can't clear it or aren't
        sure, don't look at the tab at all. Describe the setup below as clicks for them, one at a
        time, ask what they see, and ask them to check the six permissions are in the box.
   - If a Facebook window opens by itself at any point (picking the app or "User or Page" can do
     that), stop. That window is theirs. Give them the dialog message from step 6, and be hands
     off the Explorer tab from then on, just as after Generate Access Token. When it closes,
     describe the rest of the setup below as clicks for them, so the six permissions are in the
     box, and have them click Generate Access Token for the final token.
   - In the "Meta App" dropdown, pick their app.
   - In "User or Page", pick "User Token".
   - In the Permissions box, use "Add a Permission" to add these six, one at a time. The box is
     fiddly, so type each name, pick it from the list, and check it appears before the next.
     1. `instagram_basic`
     2. `instagram_manage_comments`
     3. `instagram_manage_messages`
     4. `pages_show_list`
     5. `pages_read_engagement`
     6. `business_management`
   - Leave out `pages_manage_metadata`. Meta says it isn't offered for this app, and the bot
     doesn't need it. Older guides list it.
   - Before they generate, and only if they told you the "Access Token" box is empty, take one
     screenshot and check the six permissions are there and the right app is picked. Picking the
     app can fill the box again by itself, so ask them first whether it's still empty.

6. **They make the token.** Tell them what to click and what the Facebook windows will ask,
   all in one message, because you won't be looking at the page while the token shows.

   > Now please click the blue "Generate Access Token" button. A Facebook window opens and asks a
   > few questions. Here's what to choose.
   >
   > 1. Continue as yourself.
   > 2. When it asks about Pages, choose "Opt in to current pages only" and tick your Page.
   > 3. When it asks about businesses, choose "Opt in to current businesses only".
   > 4. When it asks about Instagram, tick your Instagram account.
   > 5. Then save, or continue, until the window closes.
   >
   > The token then appears in the "Access Token" box. Tell me when the window has closed, or if it
   > asks something different.

   From the moment they click Generate Access Token, make no page reads, finds or screenshots of
   the Explorer tab until the token is in the window and you've closed the tab. If they get stuck
   in the dialogs, ask them to describe what they see.

7. **The token goes in.** Run `bash scripts/ask_key.sh FB_USER_TOKEN`. Tell them to click in the
   "Access Token" box, select all of it, copy it, and paste it into the window. There's often a
   small copy button at the end of the box that does the same.
   - When they say it's saved, run `uv run igdm keys`. `FB_USER_TOKEN` should say `set`.
   - Then close the Explorer tab with the tab tools, which read nothing. If you can't, ask them
     to close it.

8. **Connect, straight away.** Run `uv run igdm connect`. It swaps the token for a Page token that
   never expires, and saves `IG_PAGE_TOKEN`, `FB_PAGE_ID` and `IG_ACCOUNT_ID` in their keys file.
   It never prints a token or the secret. It prints two lines, `Page: <their Page> (id …)` and
   `Instagram: @<their username> (id …)`.
   - Its last line ends with "Next: uv run igdm check". Ignore that hint for now. The check needs
     the reel set up, so it first runs in chapter 7, and then again in chapter 10.
   - If it lists several Pages and says to pick one, ask which Page is for this Instagram, and run
     `uv run igdm connect --page "Page name"`, with the name or the id. If the name has an
     apostrophe in it, use the id.
   - If that says "no Page named or with id … has a linked Instagram account", the name didn't
     match. Copy the exact Page name, or the Page id, from the list it printed, and run it again.
   - If it fails, see "If it doesn't work" below. A token error usually means making a new token,
     from step 5.

## Check

`uv run igdm connect` printed their Page name and their Instagram username, and they match what
they told you in chapters 2 and 3. Run `uv run igdm keys` once more. `IG_PAGE_TOKEN`,
`FB_PAGE_ID` and `IG_ACCOUNT_ID` should all say `set`.

Celebrate this one warmly, in a sentence or two:

> You're connected to Instagram! The bot can now see your Page and your Instagram account. That
> was the hardest part of the whole setup, and it's done.

Then add `05-connect` to `done` in `.setup-progress.json`, with a note like
`"05-connect": "connected to Instagram as @theirname"`. Never write a token or the app secret in
the note (see [../reference/progress.md](../reference/progress.md)).
Then go to chapter 6.

## If it doesn't work

- **`code=190`, or "Error validating access token", or "Session has expired".** The token ran out
  or was made wrong. Make a new one from step 5, put it in with
  `bash scripts/ask_key.sh FB_USER_TOKEN`, and run `uv run igdm connect` straight away.
- **`code=1` with "Error validating client secret", or `code=101` with "Invalid application
  ID".** The app secret or the App ID is wrong, or belongs to a different app. Put them in again
  with `bash scripts/ask_key.sh META_APP_SECRET` and `bash scripts/ask_key.sh META_APP_ID`, from
  step 2, and check the app picked in Graph API Explorer is the same app. Then run
  `uv run igdm connect` again. Only make a new token if more than an hour has passed since it was
  made.
- **"Facebook did not return a long-lived token".** This is rare. Facebook answered without the
  token. Check the App ID and secret are for the app picked in Graph API Explorer, make a new
  token, and connect again.
- **"no Facebook Page with a linked Instagram account".** It lists the Pages it found. If their
  Page is missing, they didn't tick it in the "Opt in to current pages only" dialog, so make a new
  token and tick it. If the Page is there but has no Instagram, go back to chapter 3. If their
  Instagram account wasn't ticked in the dialog, make a new token too.
- **"no Page token" and a mention of pages_show_list.** That permission is missing from the
  token. Make a new token with all six permissions.
- **`(#100) The Page is not linked to an Instagram account or the linked IG account is not a
  professional account`.** Go back through chapters 2 and 3.
- **`code=200` with "Cannot call API for app … on behalf of user", or "API access blocked".**
  Meta wants a security check. Ask them to log in to facebook.com in Chrome and finish any check
  it shows, then try again. The developer dashboard usually shows nothing about it.
- **A permission is greyed out or missing in Graph API Explorer.** The app doesn't have it yet. Go
  back to chapter 4, step 10, and check the lookalike names.
- **The ask_key window doesn't open.** Use `--print`, as in
  [../reference/who-does-what.md](../reference/who-does-what.md).
- **They pasted a secret into the chat.** Don't repeat it. Tell them it's no longer private, and
  help them make a new one. For the app secret, that's "Reset" next to it on the Basic page, which
  is theirs to click. For the token, make a new one from step 5.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md) and
  [../reference/meta-notes.md](../reference/meta-notes.md).

## On Windows

The bot runs on the server, so every `igdm` command in this chapter runs there, and the secrets go
straight into the server's keys file.

- Before step 2, check the server has the settings file, with
  `ssh igdm 'test -f app/config.toml && echo yes || echo no'`. If it says `no`, run
  `bash scripts/push_config.sh` first, because `igdm connect` reads it.
- Each secret goes in with `--server`.
  - `bash scripts/ask_key.sh META_APP_ID --server`
  - `bash scripts/ask_key.sh META_APP_SECRET --server`
  - `bash scripts/ask_key.sh FB_USER_TOKEN --server`
- Confirm them with `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
- Connect with `ssh igdm 'cd app && ~/.local/bin/uv run igdm connect'`. If several Pages come back,
  use the Page's id from the list it printed, with
  `ssh igdm 'cd app && ~/.local/bin/uv run igdm connect --page <Page id>'`. Use the id and not
  the name, because an apostrophe in a Page name breaks the quotes in this command.
- There's no macOS Terminal question on Windows. The window that opens is a PowerShell window,
  and it asks for the secret the same way.
