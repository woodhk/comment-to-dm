# Chapter 13: The cloud server

**On Windows, this chapter comes straight after chapter 1.** Do every step except steps 12
and 13 (the rehearsal and the move), because there's no bot on their computer to move. Nothing
is started on the server yet. The differences are in "On Windows" at the bottom. Read it before
you start.

## Why this matters

A server is a computer that someone else keeps switched on in a data centre. They rent a small
one from Hetzner for a few dollars a month, and the bot lives there instead of on their own
computer. Then it keeps going day and night, with the laptop closed.

Three things keep it safe and looked after.

- Tailscale is a private network between their own devices. The dashboard is only reachable on
  it, so nobody else on the internet can see it.
- SSH is a secure way to type commands on another computer. You use it to set the server up and
  to run the bot's commands there, with a key file on their computer instead of a password.
- Alerts go to their phone through the ntfy app, and healthchecks.io tells them if the bot ever
  stops checking in.

Before you start, read `.setup-progress.json`. If `notes["13-cloud"]` has any note other than
"skipped for now", a server may already exist, even if the note only says the buy button was
about to be clicked. Open the Hetzner server list first (https://console.hetzner.com/, then their
project) and look. If a server is there, never buy another one. Carry on from the step after the
note.

## Steps

Do these one at a time, and wait after each. Keep the VPN off for the whole chapter, if they
have one, because it can fight Tailscale and ntfy.

1. **Say what's coming.** Tell them:

   > Now we'll set up your own small server, so the bot keeps running when your computer is off.
   > It costs a few dollars a month, and I'll check the live price and tell you before you buy
   > anything. On the way you'll make free accounts with Tailscale and healthchecks.io, and
   > install two apps on your phone. It takes about an hour.

2. **Sign up to Hetzner.** Hetzner is the company that rents the server. Warn them that Chrome
   may ask whether you can use hetzner.com (see [../reference/chrome.md](../reference/chrome.md)),
   then open https://accounts.hetzner.com/signUp.
   - The sign-up, the email confirmation, the card and any identity check Hetzner asks for are
     all theirs. Tell them what the page asks for, then wait.
   - Tell them before they start that Hetzner checks new accounts. It may approve straight away,
     ask for a small prepayment or an ID photo, hold the account for a review, or refuse it.
     - If it's held for a review, write the note `"13-cloud": "Hetzner account under review"`.
       Tell them kindly that we stop here until Hetzner emails them, and that "continue setup"
       picks up from this step.
     - If it's refused, say plainly that this guide doesn't cover another server company yet. On
       Windows, the setup can't go further without a server. If they have a Mac, the bot can run
       on the Mac instead, and the setup works from there.
   - When they're in, open https://console.hetzner.com/ and help them make a project. Call it
     `igdm`.

3. **Choose the server.** In the project, click "Create server" (or "Add server"). You fill in the
   choices, one at a time, saying what each one is.
   - **Location.** One near them is fine. If the page says "Preselected server type is not
     available", pick another location.
   - **Image.** Pick **Ubuntu 24.04**. The page often picks a newer Ubuntu by default, so check
     it. The setup script is written for 24.04.
   - **Type.** Pick the cheapest shared type that's available there.
   - **Networking.** Leave public IPv4 switched on. It makes the first connection simple.
   - **Backups.** Leave it off. Hetzner charges extra for it, and the bot makes its own nightly
     backup.
   - **Name.** `igdm`.

4. **Add the key.** An SSH key is a pair of files. The private half stays on their computer and
   is never shown to anyone. The public half is safe to share, and it goes on the server so it
   knows their computer.
   - Run `bash scripts/ssh_setup.sh key`. It makes the key once, and prints where the public half
     is, `~/.ssh/igdm_ed25519.pub`. It never prints the private half.
   - Read the public half with `cat ~/.ssh/igdm_ed25519.pub`. It's one line starting
     `ssh-ed25519`. Never open the file without `.pub`.
   - Tell them in a sentence that the private half lives only on this computer, so a new computer
     later means getting in through the Hetzner console. If they like, they can keep a private
     copy of `~/.ssh/igdm_ed25519` somewhere safe, like a password manager. That copy is theirs to
     make, and you never open the file.
   - On the create page, in "SSH keys", click "Add SSH key". Set the key box's value directly
     with the line, as [../reference/chrome.md](../reference/chrome.md) says. Typing it in can
     give "SSH key has invalid format". Name the key `igdm` and add it. Check it's ticked.

5. **Say the price, then they buy.** Read the price for the whole server off the page now,
   including the IPv4 address if it's listed on its own, and tax if the page shows it. Say it as
   a monthly amount. For example:

   > The server will cost about [the monthly total from the page] a month. That's the only part
   > of the cloud setup that costs money, and you can delete the server any time to stop paying.
   > If that's OK, please click "Create & Buy now".

   Right before you ask them to click, write the note `"13-cloud": "about to click Hetzner buy"`
   (see [../reference/progress.md](../reference/progress.md)). Clicking the buy button is theirs.
   The moment they say they've clicked it, change the note to `"13-cloud": "Hetzner buy
   clicked"`. When the server shows in the list, read its IPv4 address off the page, and write
   the note `"13-cloud": "Hetzner server bought, address <IPv4 address>"` straight away.

6. **Point SSH at the new server.** Run `bash scripts/ssh_setup.sh host <IPv4 address> root`. It
   says `"ssh igdm" now goes to root@<address>.` Root is the server's all-powerful user, which you
   only need for the setup.
   - Then connect once, so their computer learns the server's fingerprint:
     `ssh -o StrictHostKeyChecking=accept-new igdm true`. It should finish with no error. A new
     server can take a minute to start, so if it says "Connection refused" or times out, wait a
     minute and try again.

7. **Set the server up.** Tell them it takes about 5 to 10 minutes, and that in the middle they'll
   sign in to Tailscale.
   - Find their timezone with `grep '^timezone' config.toml`. If it still says `UTC` and they
     don't live in UTC, ask them which city to use.
   - Copy the setup files across with `scp -r deploy igdm:`.
   - Then run this with the Bash tool's background option, so you can watch its output while it
     runs. If you don't have a background option, run it in the foreground with the timeout set
     to 600000 ms. If it stops at the timeout, read what it printed. When the last lines show the
     Tailscale sign-in link, open it, let them approve the server, and then run the same command
     again. The script is safe to run again, and it skips the parts already done. Never
     interrupt it yourself.

     ```
     ssh -t igdm bash deploy/setup_server.sh <their timezone> https://github.com/woodhk/comment-to-dm.git
     ```

     A line saying "Pseudo-terminal will not be allocated" is normal. Watch the output with the
     Monitor tool until it contains `login.tailscale.com`, `Serve is not enabled` or `Done.`, or
     the command ends. If there's no Monitor tool, read the output each time they reply, or when
     the foreground run returns. Don't write sleep loops. It goes through sections that start with `==`,
     like `== packages` and `== firewall: nothing in except SSH`.
   - **The Tailscale sign-in.** When the output says `>>> Open the login link below in your
     browser and approve this server.`, a `https://login.tailscale.com/...` link follows. Warn them
     Chrome may ask about tailscale.com, then open the link. They make their Tailscale account or
     sign in (with Google, Microsoft, GitHub or Apple, as they like), and click "Connect" to let
     the server join. That's theirs. The script carries on by itself once they have.
   - **The HTTPS switch.** Next it may print "Serve is not enabled on your tailnet" with a link.
     HTTPS is what makes the dashboard address start with `https://`. Open the link, and they
     click to enable it. If the script stopped with an error after that link, run the same `ssh`
     command again once they've enabled it. The script is safe to run again.
   - It's finished when the last lines say `Done. Dashboard address: https://<tailscale name>`,
     then `DASHBOARD_HOSTS=<tailscale name>`, then that the bot and dashboard are installed but
     not running, and that only `scripts/cutover.sh` (Mac) or `scripts/server_start.sh` (Windows)
     starts them. The Tailscale name looks like
     `igdm.<something>.ts.net`. Write it in the note, for example
     `"13-cloud": "server set up at <IPv4 address>, Tailscale name <tailscale name>"`.
   - If it stopped with an error anywhere else, read the lines above it and follow
     [../reference/stuck.md](../reference/stuck.md).
   - **Keep the server on their network.** Open https://login.tailscale.com/admin/machines. On
     the `igdm` machine's row they click the "..." menu, then "Disable key expiry". That click is
     theirs. Tell them why in one sentence:

     > Otherwise Tailscale asks the server to sign in again after about 180 days, and until it
     > does, the server drops off your private network and we can't reach it.

     The Check at the end confirms it took.

8. **Tailscale on their computer and phone.** Their devices join the same private network, so
   they can reach the server and the dashboard. Do it one device at a time.
   - **Computer.** Open https://tailscale.com/download, and they install the app for their
     computer. The install, any "Allow" for a VPN setting and the sign-in are theirs. They sign in
     with the same account as before. When it's on, the Tailscale icon shows they're connected.
   - **Phone.** They install the Tailscale app from the App Store or Google Play, sign in with the
     same account, and switch it on. The phone asks to add a VPN configuration, and they tap
     Allow.
   - Warn them about a VPN, in words like these:

     > Tailscale works like a small VPN of its own. If you use another VPN on your computer or
     > phone, the two can fight, and the dashboard or the alerts may stop working. If something
     > here doesn't connect, turning the other VPN off is the first thing to try.

9. **Switch SSH to Tailscale.** From now on you reach the server over Tailscale, as the bot's own
   user `igdm`, not root.
   - Run `bash scripts/ssh_setup.sh host <tailscale name>`. The user is `igdm` when none is given.
     It says `"ssh igdm" now goes to igdm@<tailscale name>.`
   - Connect once: `ssh -o StrictHostKeyChecking=accept-new igdm true`. If it times out, Tailscale
     isn't on, on their computer. If it says "Could not resolve hostname", see "If it doesn't
     work". Only go on to the lock when this works, because the lock shuts every other way in.
   - Now close the server to everyone else. The lock script has to run as root, so run it with
     root over the same Tailscale connection:

     ```
     ssh -l root igdm bash deploy/lock_ssh_to_tailscale.sh
     ```

     It ends by showing the firewall, with port 22 allowed only `on tailscale0`. Tell them that
     the server now ignores everyone on the internet, and only their own devices can reach it.
   - Run `ssh igdm true` once more. It should work as before.
   - If you ever run `deploy/setup_server.sh` again, it opens SSH to the internet again. Run the
     lock line again afterwards.

10. **Alerts on their phone (ntfy).** ntfy is a free app that shows the bot's alerts on their
    phone, like "Instagram blocked the bot".
    - They install **ntfy** from the App Store or Google Play. Tell them the one they want is
      called just "ntfy", with a white bell on a green or teal square. It isn't "ntfy me", which
      is a different app. They open it and allow notifications.
    - You make the topic, the private name their phone listens on. Follow the ntfy topic rule in
      [../reference/who-does-what.md](../reference/who-does-what.md). On a Mac it goes into the
      keys file on the Mac, because steps 12 and 13 copy that file to the server:

      ```
      TOPIC="igdm-$(openssl rand -hex 12)"; printf '%s\n' "$TOPIC" | uv run igdm set-key NTFY_TOPIC --stdin && echo "$TOPIC"
      ```

      On Windows it goes straight onto the server, with the line in "On Windows" below.
    - Show them the topic once, from the command's output. In the ntfy app they tap the + button
      to subscribe to a topic, type the topic exactly, leave the server as it is, and tap
      Subscribe. If it times out, a VPN is usually why.
    - Write the note "ntfy topic set". Never write the topic itself anywhere.

11. **healthchecks.io.** It watches for a bot that has stopped. The bot checks in every minute,
    and if it goes quiet, healthchecks.io emails them.
    - Warn them Chrome may ask about healthchecks.io, then open https://healthchecks.io/ and click
      "Sign Up" on the home page. Don't open the sign-up page's address directly, because it gave
      an error before. They type their email, and they sign in from the link it emails them.
      That's theirs. The link may open in a different browser from Chrome. If it does, ask them
      to copy the link's address from the email and paste it into Chrome's address bar instead,
      so they're signed in where you can help.
    - A check's own page shows its ping URL, a private link that lets anyone pretend to be their
      bot. So the hands-off rule starts here (see [../reference/chrome.md](../reference/chrome.md)).
      Once they're signed in, make no page reads, finds or screenshots of that tab until they say
      the ping URL is in the window and they've left the check's page (for example, gone back to
      the healthchecks.io home page). Check the tab title first, and only read the page again once
      the ping URL can't be on it. Describe the clicks instead, one at a time.
      - A new account often has a check already, made for them. If there is one, they open it
        and rename it instead of adding a second one. If there's none, they click "Add Check".
      - They name it `igdm`.
      - They click "Change Schedule" (or the schedule settings), set Period to 1 minute and Grace
        Time to 10 minutes, and save.
    - Open the window for the ping URL. On a Mac it goes into the Mac's keys file,
      `bash scripts/ask_key.sh HEALTHCHECKS_URL`, for the same reason as the topic. On Windows it's
      `bash scripts/ask_key.sh HEALTHCHECKS_URL --server`. Say the words from who-does-what.md
      about the window, and ask them to copy the ping URL shown on the check's page (it starts
      `https://hc-ping.com/`) into it.
    - When they say it's saved, confirm it with `uv run igdm keys` on a Mac, or the server form on
      Windows. `HEALTHCHECKS_URL` and `NTFY_TOPIC` should both say `set`.
    - Tell them the check stays grey until the bot runs there, and that's normal.

12. **Mac only. The rehearsal.** This sends a test alert from the server, while the bot on their
    Mac keeps running.
    - First make sure the Mac and GitHub have the same version, because the move refuses
      otherwise. Run `git fetch -q origin`, then `git rev-parse HEAD origin/main`. If the two
      lines differ, update the Mac first with the update skill
      ([../../update-my-bot/SKILL.md](../../update-my-bot/SKILL.md)).
    - Run `bash scripts/rehearse.sh`. It copies their keys file and settings file to the server,
      runs `igdm check` there, and sends a test alert with `igdm test-alert`.
    - Their phone should get "Test alert" in ntfy, and their inbox may get the same email. It
      ends with `The Mac bot is still running.`
    - If the check complained about Meta or Google, fix that before the move.

13. **Mac only. The move.** Tell them:

    > Now we'll move the bot to the server, with every person it's talking to and everything it
    > remembers, so nobody gets a second DM. It stops on your Mac first, then starts on the
    > server. It takes a few minutes, and if anything goes wrong before the server starts, your
    > Mac carries on as before.

    - Run `bash scripts/cutover.sh`. Run it with the Bash tool's timeout set to 600000 ms, or in
      the background and watch it to the end. Never interrupt it. It goes through numbered steps,
      from `== 1. checks` to `== 8. backups to this Mac`, and waits up to 5 minutes for the server
      bot's first check.
    - It ends with `Done. The bot runs on the server now.` and the dashboard address.
    - If it says `Your Mac and GitHub have different versions. Say "update my bot" first, then
      move.`, update the Mac with the update skill, then run it again.
    - If it says `Commit or discard local changes first.`, it lists the changed files. Show them
      what changed and ask what they'd like to keep. Never discard anything without asking.
    - Celebrate it. Their bot is on the server now.

14. **Nightly backups.** These are already on, so there's nothing to do. Tell them in a sentence
    or two.
    - Every night at 03:30, their time, the server saves a copy of the bot's database and settings,
      and keeps 14 days of them.
    - **Mac.** The move also set up a small background job on the Mac (with
      `scripts/install_agent.sh --backups-only`) that copies them to the `igdm-backups` folder in
      their home folder whenever the Mac is awake.
    - **Windows.** They stay on the server, 14 days of them. They start once the bot does.

## Check

**Mac and Windows.** Key expiry is off on the server. Run

```
ssh igdm "tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"Self\"].get(\"KeyExpiry\",\"none\"))'"
```

- `none` means expiry is off. That passes.
- A date means expiry may still be on. If the date is within the next 6 months, it's still on.
  Ask them to do "Disable key expiry" from step 7 again, then run this once more. A date many
  years away is fine.
- Anything else, like an error, means Tailscale printed something this check doesn't know. Don't
  guess. Open https://login.tailscale.com/admin/machines and ask them whether the `igdm` row still
  shows an expiry date. If it doesn't, that passes.

**Mac.** All of these pass.

- `test -f data/MOVED_TO_SERVER && echo moved` prints `moved`.
- `ssh igdm 'tail -n 20 app/logs/igdm.log'` shows a `tick` line from the last minute or two.
- `ssh igdm 'systemctl is-active igdm igdm-dashboard'` prints `active` twice.
- With Tailscale on, `https://<tailscale name>` opens the dashboard in Chrome and shows their
  reel's numbers. Show them, and suggest they bookmark it on the computer and the phone.

Then add `13-cloud` to `done`, with a note like
`"13-cloud": "bot moved to the server on 2026-11-09, dashboard https://<tailscale name>"`. Tell
them a few things have changed.

- The dashboard is the `https://` address now, not `127.0.0.1`. Tailscale has to be on, on the
  device they open it on.
- Their Mac won't start its own bot any more, which stops people getting two DMs.
- To change settings, you'll run `bash scripts/pull_config.sh` first, edit, then
  `bash scripts/push_config.sh`.

Then show the everyday card ([everyday.md](everyday.md)).

**Windows.** All of these pass.

- `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'` runs, and shows `NTFY_TOPIC` and
  `HEALTHCHECKS_URL` as `set`.
- They confirm the "Test alert" from the test alert step arrived on their phone.
- The PowerShell check in "On Windows" below has run, so you know which way key windows open.

Add `13-cloud` to `done`, even though nothing is started yet, with a note like
`"13-cloud": "server ready at <tailscale name>, nothing started yet"`. If the PowerShell check
failed, add "key windows use --print" to that note. Then go to chapter 2.

## If it doesn't work

- **Hetzner says "Preselected server type is not available".** That type is sold out in that
  location. Pick another location, or the next cheapest type, and say the new price.
- **"SSH key has invalid format".** The key was typed in, not set. Clear the box and set its value
  directly with the `.pub` line.
- **The first `ssh igdm true` says "Permission denied (publickey)".** The key wasn't added to the
  server when it was made. The simplest fix is to delete the server and make a new one with the
  key ticked. Hetzner charges by the hour, so the deleted one costs only cents, but ask them first,
  and say the price again before they buy. Update the note with the new address.
- **`Host key verification failed` or a warning that the host identification has changed.** Their
  computer remembers an older server under the same address. Run
  `ssh-keygen -R <address>`, then the `accept-new` line again.
- **`Unknown timezone`.** The name is spelled differently from the list. Ask the server with
  `ssh igdm 'timedatectl list-timezones' | grep -i <city>`, and use the line that matches.
- **The Tailscale link isn't in the output yet.** It comes after the packages, which can take
  several minutes. Keep reading the output.
- **`The app folder already has files that didn't come from git.`** Something was copied into the
  server's app folder before. Read the folder with `ssh igdm 'ls -la /home/igdm/app'` and follow
  [../reference/stuck.md](../reference/stuck.md). Don't delete anything there without asking.
- **`ssh igdm true` over Tailscale times out.** Tailscale is off on their computer, or another VPN
  is fighting it. Ask them to check the Tailscale app says connected and turn any other VPN off.
  Run `ssh igdm 'tailscale status'` only once it connects.
- **`ssh igdm true` says "Could not resolve hostname".** Their computer can't look up the
  Tailscale name. First ask them to check the Tailscale app on their computer is connected, and
  that its setting for Tailscale DNS ("Use Tailscale DNS settings", or close to it) is switched
  on. Then try again. The dashboard address needs the name too, so this is the better fix. If it
  still fails and the lock in step 9 hasn't run yet, use the server's Tailscale address instead.
  Get it with
  `ssh -i ~/.ssh/igdm_ed25519 -o IdentitiesOnly=yes root@<IPv4 address> 'tailscale ip -4'`,
  which prints an address starting `100.`. Run `bash scripts/ssh_setup.sh host <that address>`,
  then the `accept-new` line again. The Tailscale app also shows that address next to `igdm`.
- **They can't get in at all after the lock.** First check the Tailscale app on their computer
  says connected, and that other VPNs are off. If it still fails, open SSH to the internet again
  for a moment, so you can fix the rest yourself. They only type one line.
  1. In the Hetzner console, the server's "Rescue" tab can reset the root password. That
     password is a secret, so follow the hands-off rule in
     [../reference/chrome.md](../reference/chrome.md) from the moment they click it.
  2. They click the console button (it looks like `>_`), which opens a window on the server, and
     log in as `root` with that password.
  3. They type `ufw allow OpenSSH` and press Enter. That's all they type there, and they can
     close the console window afterwards.
  4. You run `bash scripts/ssh_setup.sh host <IPv4 address> root`, then
     `ssh -o StrictHostKeyChecking=accept-new igdm true`.
  5. You run `ssh igdm 'tailscale status'`. If it says the server is logged out or stopped, run
     `ssh igdm 'tailscale up --hostname=igdm'` in the background, open the link it prints, and they approve it as
     in step 7. Then do "Disable key expiry" from step 7 again.
  6. Switch back as in step 9, with `ssh_setup.sh host <tailscale name>` and the `accept-new`
     line, and run the lock line again.
- **The server dropped off Tailscale after a few months.** Its key expired. Do the steps above,
  which end with `tailscale up` and "Disable key expiry".
- **ntfy times out when subscribing.** A VPN is on. Turn it off and subscribe again.
- **The test alert says `ntfy: FAILED`.** Check the topic in the app is exactly the one you showed
  them. If they mistyped it, unsubscribe and subscribe again.
- **`rehearse.sh` says `The server is already running the bot`.** It was moved already. Check
  `data/MOVED_TO_SERVER` and the server's log before doing anything else.
- **`cutover.sh` says `Cutover failed before the server sent anything. Putting the Mac back...`**
  Nothing changed and the Mac is running the bot again. Read the lines above it, fix that, and run
  it again.
- **`cutover.sh` says `Cutover failed after the server bot started.`** Read the server's log with
  `ssh igdm 'journalctl -u igdm -n 50'`. If it can't be fixed there, `bash scripts/rollback.sh`
  moves the bot back to the Mac with every lead. Ask them before you run it.
- **The dashboard address doesn't open.** Tailscale has to be on, on that device. If it is, run
  `ssh igdm 'systemctl is-active igdm-dashboard'` and read
  `ssh igdm 'journalctl -u igdm-dashboard -n 50'`.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

This chapter comes straight after chapter 1, before the Instagram parts, because every `igdm`
command from chapter 2 on runs on the server. Use Git Bash for everything. The first time they
need it themselves, say "Open the Start menu, type Git Bash, and press Enter."

- **Steps 1 to 11 are the same,** with these differences.
  - In step 8, they install Tailscale for Windows from https://tailscale.com/download. Windows may
    ask whether to allow it to make changes, and that click is theirs.
  - After step 9, check that Windows' own ssh can reach the server too. The key windows from
    `ask_key.sh --server` are PowerShell windows, and they use Windows' own ssh, not Git Bash's.
    It can be missing, look in a different folder for the key, or stop to ask about the server's
    fingerprint, and a secret pasted into that question would be lost. Run this from Git Bash:

    ```
    powershell.exe -NoProfile -Command "ssh -o BatchMode=yes igdm true; exit \$LASTEXITCODE"
    ```

    If it ends with exit code 0, the PowerShell windows work. Anything else (an error, "not
    recognized", "Host key verification failed" or "Permission denied") means they don't. Then
    use the `--print` way for every secret instead, as in
    [../reference/who-does-what.md](../reference/who-does-what.md). They open Git Bash
    themselves, run the line it prints, and paste the secret there. Remember it for the Check.
  - In step 10, the topic goes straight onto the server:

    ```
    TOPIC="igdm-$(openssl rand -hex 12)"; printf '%s\n' "$TOPIC" | ssh igdm 'cd app && ~/.local/bin/uv run igdm set-key NTFY_TOPIC --stdin' && echo "$TOPIC"
    ```

  - In step 11, the ping URL goes in with `bash scripts/ask_key.sh HEALTHCHECKS_URL --server`. The
    window is a PowerShell window, and there's no macOS question first. Confirm it with
    `ssh igdm 'cd app && ~/.local/bin/uv run igdm keys'`.
- **The test alert, in place of steps 12 and 13.** There's no bot on their computer to move, so
  skip the rehearsal and the move. The test alert needs the settings file on the server.
  - Run `bash scripts/push_config.sh`. This is its first push, so it sends `config.toml` from
    chapter 1 and checks it on the server.
  - Then run `ssh igdm 'cd app && ~/.local/bin/uv run igdm test-alert'`. It prints `ntfy: sent`,
    and their phone gets "Test alert". There's no email line yet, because Gmail comes in chapter 8.
    A warning line, `email sending is off: GMAIL_APP_PASSWORD missing in .env`, is expected here
    for the same reason.
- **Nothing is started.** The bot and the dashboard stay off until the end of the setup, so don't
  open the dashboard address yet. Later, in place of chapter 12, `bash scripts/server_start.sh`
  starts them. Run it with the Bash tool's timeout set to 600000 ms, or in the background and
  watch it to the end. Never interrupt it.
- **Step 14.** The nightly backups stay on the server, 14 days of them, once the bot is started.
- **The Check** is the Windows one above. When it passes, add `13-cloud` to `done` and go to
  chapter 2.
