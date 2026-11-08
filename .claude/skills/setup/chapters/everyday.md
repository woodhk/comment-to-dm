# The everyday card

Show this at the end of setup, and again whenever they ask how to use the bot day to day. Put it
in your own words if you like, but keep the voice and keep it short. Use the dashboard address
that fits them: `http://127.0.0.1:8787` when the bot runs on their Mac, or the server address from
chapter 13 (it looks like `https://igdm.<something>.ts.net`) when it runs on the server.

## The card

> Here's how to look after your bot from now on. You can always ask me again.
>
> **Your dashboard.** Open [the dashboard address] in your browser. If your bot is on the cloud
> server, Tailscale has to be switched on, on that device. There are four boxes at the top.
> "Emails" is how many email addresses you've collected. "DM to email" is how many people who got
> a DM gave their email. "Needs you" is how many things are waiting for you. "AI spend" is what
> the AI has cost so far.
>
> **The "Needs you" part.** This is the only bit that needs you. There are a few kinds of card.
>
> - A "needs info" card means the bot is missing something, like an email that's in the person's
>   bio. Find it, paste it into the card and press "Save and send". The bot thanks them and sends
>   the material a couple of minutes later.
> - A "needs you" card is a comment the bot left for you, like a complaint, a business pitch, or
>   anything the AI wasn't sure about. Reply to them in Instagram yourself, then press "Done". If
>   you've sorted it and they still want the material, press "Let the bot carry on" instead.
> - An email card is an email that didn't go out cleanly. Press "Send now", or press "It already
>   went out" if your Gmail Sent folder shows it did.
>
> **A new reel.** Open the Reels tab and press "New reel". Paste the reel's link, then fill in the
> keyword, what you're giving away, and the link to the material. The bot picks it up within a
> minute. You can pause a reel from the same tab.
>
> **Answering people yourself.** Please don't DM someone by hand in the minute after they comment,
> because the bot's DM is already on its way. If you want to handle someone yourself, reply to
> their comment publicly instead, and the bot leaves them alone. Once the bot's DM has gone out,
> it notices if you type in that chat and steps back.
>
> **If Instagram blocks the bot.** Sometimes Meta switches off the bot's access, and the dashboard
> shows "Instagram access is blocked". Come back here and say "my bot is blocked", and I'll help
> you fix it. Usually you just log in to facebook.com in Chrome and pass the security check it
> shows.
>
> **Updates.** Meta changes things now and then, so the bot gets updates. When you'd like the
> newest version, open Claude Code in this folder and say "update my bot". Your settings, keys
> and leads stay exactly where they are.
>
> **Coming back to me.** [The steps below that fit them.] Then say what you need, like "update my
> bot" or "continue setup".

For "Coming back to me", give the steps that fit them, one per line.

- **Windows.** Open the Start menu, type Git Bash, and press Enter. Type `cd comment-to-dm` and
  press Enter, then type `claude` and press Enter. Check the Tailscale icon says connected first,
  because I reach your server through it.
- **Mac, in Terminal.** Press Command and Space, type Terminal, and press Enter. Type
  `cd ~/comment-to-dm` and press Enter, then type `claude` and press Enter. If the bot is on the
  server, check Tailscale says connected too.
- **Mac or Windows, in the Claude desktop app.** Open the Code tab, start a new session, and pick
  the `comment-to-dm` folder in your home folder.

Use the folder they actually have, if it isn't `~/comment-to-dm`. Run `pwd` to see it.

## When Meta blocks access (for you)

Read the error first, from the dashboard banner, the alert, or the log (see
[../reference/stuck.md](../reference/stuck.md)). Then match it.

- **`code=200`, "Cannot call API for app … on behalf of user", or "API access blocked".** Meta
  wants a security check. The developer dashboard usually says nothing about it.
  1. They log in to facebook.com in Chrome, with the account that manages their Page, and finish
     any check it shows.
  2. Run `uv run igdm check` (or the server form). The banner clears by itself once the bot can
     reach Instagram again.
- **`code=190`, "The session has been invalidated because the user changed their password or
  Facebook has changed the session for security reasons".** The bot's lasting token stopped
  working. This happens after a Facebook password change, or if the app or a permission was
  removed.
  1. Make a new token in Graph API Explorer, exactly as in
     [05-connect.md](05-connect.md), and put it in with `bash scripts/ask_key.sh FB_USER_TOKEN`
     (add `--server` if the bot runs on the server).
  2. Run `uv run igdm connect`, or `ssh igdm 'cd app && ~/.local/bin/uv run igdm connect'` on the
     server. Their Page name and Instagram username should come back.
  3. Restart the bot and the dashboard so they use the new token. On a Mac that's
     `launchctl kickstart -k gui/$(id -u)/local.igdm` and then
     `launchctl kickstart -k gui/$(id -u)/local.igdm-dashboard`. On the server it's
     `ssh igdm 'sudo systemctl try-restart igdm igdm-dashboard'`.
  4. Run `igdm check` (or the server form) to confirm.
- **`(#100) The Page is not linked to an Instagram account`.** Go through
  [02-instagram.md](02-instagram.md) and [03-facebook-page.md](03-facebook-page.md) again.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md) and
  [../reference/meta-notes.md](../reference/meta-notes.md).

## Other everyday questions (for you)

- **"Is it running?"** Look at the last lines of the log, with `tail -n 20 logs/igdm.log` on a
  Mac or `ssh igdm 'tail -n 20 app/logs/igdm.log'` on the server. A line appears every minute.
- **"Who's waiting on me?"** `uv run igdm status`, or the server form.
- **"Can I get my emails in a spreadsheet?"** The file holds their followers' email addresses,
  so it goes in their Downloads folder, not the project folder, and nowhere else.
  - **Bot on the Mac.** `uv run igdm emails --csv ~/Downloads/emails.csv`
  - **Bot on the server.** Make it there, copy it to Downloads, then remove the server's copy:
    `ssh igdm 'cd app && ~/.local/bin/uv run igdm emails --csv data/emails.csv'`, then
    `scp igdm:app/data/emails.csv ~/Downloads/emails.csv`, then
    `ssh igdm 'rm -f app/data/emails.csv'`.

  Tell them it's in Downloads as `emails.csv`, that it opens in any spreadsheet app, and to keep
  it private.
- **Changing the wording.** Edit `[messages]` in `config.toml` for them, read it back, and let the
  bot reload it within a minute. If the bot runs on the server, first run
  `bash scripts/pull_config.sh` so you edit the server's newest copy, then send it back with
  `bash scripts/push_config.sh`.
- **"Update my bot."** That's the update skill, so let it take over.
