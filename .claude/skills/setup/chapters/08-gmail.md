# Chapter 8: Gmail

## Why this matters

When someone gives their email address in a DM, the bot emails them the material from the
creator's own Gmail. To send from Gmail, the bot needs an app password. An app password is a
16-letter password Google makes for one program. It only lets that program send email, it isn't
their normal Gmail password, and they can delete it any time without changing anything else.

Google only offers app passwords when 2-Step Verification is on. 2-Step Verification means Google
asks for a code from their phone, as well as the password, when someone signs in somewhere new.

## Steps

Do these one at a time.

1. **Which Gmail.** Ask which Gmail address the material should come from. Most people use the
   address they already share with their followers.
   - If it doesn't end in `@gmail.com`, it may be a Google Workspace account (a work or school
     Google account on its own domain). Tell them that the person who runs that account, the
     admin, may have switched app passwords off. If the page in step 4 says the setting isn't
     available, that's why. They can ask the admin, or use a normal Gmail address instead.

2. **Write it into the settings file.** In `config.toml`, under `[email]`, set `from_address` to
   their address, like `from_address = "sam@gmail.com"`. Leave `enabled = true`. Tell them what
   you changed. The `from_name` line comes in chapter 9, with their name.

3. **2-Step Verification first.** Open https://myaccount.google.com/security. Warn them first that
   Chrome may ask whether you can use myaccount.google.com. If Google asks them to sign in, that's
   theirs.
   - Under "How you sign in to Google", look at "2-Step Verification". If it says it's on, go to
     step 4.
   - If it's off, they click it and follow Google's steps. It needs their phone, so tell them that
     first. Every code and every tap on the phone is theirs. Wait until they say it's on.

4. **Make the app password.** Open https://myaccount.google.com/apppasswords. Google may ask for
   their password again. That's theirs.
   - If the page says "The setting you are looking for is not available for your account", it's
     either 2-Step Verification still being off, or a Workspace admin who switched app passwords
     off (step 1). Go back to step 3 to check which one.
   - The page has an "App name" box. You may fill it in with `igdm`, because the name isn't
     secret.
   - Run `bash scripts/ask_key.sh GMAIL_APP_PASSWORD` and say the words from
     [../reference/who-does-what.md](../reference/who-does-what.md) about the window.
   - Then tell them this:

     > Now please click "Create". Google shows a box with your app password, 16 letters in groups
     > of four. Copy it, paste it into the window I just opened, and press Enter. The window
     > should say "Saved GMAIL_APP_PASSWORD." Then click "Done" on the Google page, and tell me.

   - From the moment you ask them to click Create, follow the hands-off rule in
     [../reference/chrome.md](../reference/chrome.md). Make no page reads, finds or screenshots of
     that tab until they say it's saved and they've clicked "Done".

5. **Make sure it was saved.** Before you check anything, ask:

   > Did the window say "Saved GMAIL_APP_PASSWORD."? If it's still waiting, click in it and press
   > Enter.

   This matters. If the window is still waiting, nothing is saved yet, and the test would fail for
   no clear reason. When they say yes, run `uv run igdm keys`. `GMAIL_APP_PASSWORD` should say
   `set`.

## Check

Run `uv run igdm test-gmail`. It signs in to Gmail with the app password and signs out again. It
sends nothing. It should print `Gmail sign-in works for <their address>.`

Tell them it worked in a sentence, for example "Gmail works. The bot can send your material from
your own address." Add `08-gmail` to `done` in `.setup-progress.json`, with a note like
`"08-gmail": "app password set for sam@gmail.com"`. Never write the password. Then go to
chapter 9.

## If it doesn't work

- **`GMAIL_APP_PASSWORD is not set`.** The window didn't save it. Ask what the window says, and
  open it again with `bash scripts/ask_key.sh GMAIL_APP_PASSWORD`. Google only shows the password
  once, so they may need to make a new one (step 4) and delete the unused one from the same page.
- **`Gmail refused the sign-in for …`.** Check that `from_address` in `config.toml` is the same
  Google account they made the app password in. If it is, they make a new app password and put it
  in again. The spaces between the groups of four don't matter, because the window takes them out.
- **`No Gmail address to sign in with`.** `from_address` is missing under `[email]` in
  `config.toml`. Add it (step 2).
- **`Could not reach Gmail (smtp.gmail.com)`.** The internet connection, a VPN or a work network
  is blocking it. Ask them to turn the VPN off and try again.
- **There's no "App passwords" page at all.** 2-Step Verification is off, or a Workspace admin
  has switched app passwords off. See step 1 and step 3.
- **They pasted the password into the chat.** Don't repeat it. Tell them it's no longer private.
  They delete it on https://myaccount.google.com/apppasswords and make a new one, which goes in
  through the window.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot runs on the server, so the password goes into the server's keys file, and the test runs
there.

- Write `from_address` into the local `config.toml` (step 2), then send it with
  `bash scripts/push_config.sh`. The test reads the address from the server's copy.
- The password goes in with `bash scripts/ask_key.sh GMAIL_APP_PASSWORD --server`. The window is a
  PowerShell window, and it asks the same way. It says "Saved GMAIL_APP_PASSWORD." when it's done.
- Confirm it with `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
- Run the check with `ssh igdm 'cd app && ~/.local/bin/uv run igdm test-gmail'`.
