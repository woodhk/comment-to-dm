# Chapter 12: Run it all the time

**On Windows, this chapter is different.** The bot runs on the server, so there's no background
agent to install on their computer. Skip steps 1 to 3 and do "On Windows" at the bottom instead,
which starts the bot with `bash scripts/server_start.sh`. Then do the dashboard tour (step 4) and
the first real DM (the Check) using the server's dashboard address, `https://<tailscale name>`.

## Why this matters

Until now the bot has only run when you typed a command. Now it starts running by itself, all the
time, and the real queue begins. A background agent is a small helper that macOS starts when they
log in, and starts again if it ever stops. There are two of them, the bot itself and its
dashboard, a page in their browser that shows what the bot is doing and what needs them.

On a Mac, the bot runs whenever the Mac is awake. If the Mac sleeps, it catches up when it wakes.
That's why chapter 13, the cloud server, is offered at the end, for people who'd like it to keep
going with the laptop closed.

## Steps

1. **Say what's about to happen.** Tell them:

   > Now we'll switch the bot on for real. From this moment it watches your reel, and anyone who
   > comments your keyword gets your DM about a minute later. It checks every minute, whenever
   > your Mac is awake. Your Mac may show a small note saying a background item was added. That's
   > the bot, and it's expected.

   Check `handle_comments_after` in `config.toml` with them one last time. Everyone who commented
   on the reel after that time, and isn't older than about 7 days, gets a DM once it starts, a
   few minutes apart. If the `11-live-test` note says the live test used the real reel, it must
   be the time the test ended, or later.

2. **Install it.** Run `bash scripts/install_agent.sh`. It prints a line for each agent, starting
   `Installed and started local.igdm.` and `Installed and started local.igdm-dashboard.`, and
   ends with `Dashboard: http://127.0.0.1:8787`.
   - If macOS shows "Background Items Added", tell them it's the bot and they can close the note.
     If macOS ever asks whether to allow it in the background, they click Allow.

3. **Make sure it's alive.** Wait about a minute, then run `tail -n 20 logs/igdm.log`. You should
   see `igdm running (dry_run=False, …)`, then a `tick: {…}` line. A new `tick` line appears every
   minute. Tell them in a sentence that the bot is running.

4. **The dashboard tour.** Open the dashboard in Chrome. When the bot runs on this Mac, the
   address is http://127.0.0.1:8787. It only works on this Mac, and nobody else can see it. When
   the bot runs on the server, it's the `https://` address from the server, which only opens on
   their own devices with Tailscale switched on. Show them around one part at a time, and wait
   after each.
   - **Overview, at the top.** Four boxes. "Emails" is how many email addresses they've
     collected. "DM to email" is how many people who got a DM gave their email. "Needs you" is how
     many things are waiting for them. "AI spend" is what the AI has cost so far.
   - **Needs you.** This is the only part that ever needs them. Each card is a person the bot
     handed over. Explain the three kinds, using the words on the cards.
     - A "needs info" card means the bot is missing one thing, like an email address that's in the
       person's bio. They find it, paste it into the card and press "Save and send". The bot
       thanks the person and sends the material a couple of minutes later.
     - A "needs you" card is a comment or reply the bot left for them, like a complaint, a business
       pitch, or anything the AI wasn't sure about. They reply in Instagram themselves, then press
       "Done". If they've sorted it and the person still wants the material, they press "Let the
       bot carry on" instead.
     - An email card is an email that didn't go out cleanly. They press "Send now", or "It already
       went out" if their Gmail Sent folder shows it did.
     If it says "Nothing needs you. The bot is handling everyone.", that's the normal, happy state.
   - **Funnel.** How many people went from the first DM to getting the email.
   - **Reels.** In the menu. This is where they add a new reel with "New reel", or pause one.
     The bot picks up a change within a minute.
   - **Activity.** In the menu. What the bot did most recently, person by person.

   Then tell them one rule that matters:

   > Please don't DM someone by hand in the minute after they comment, because the bot's DM is
   > already on its way. If you want to handle someone yourself, reply to their comment publicly
   > instead, and the bot leaves them alone.

## Check

- `tail -n 20 logs/igdm.log` shows a `tick` line from the last minute or two.
- http://127.0.0.1:8787 opens and shows their reel's numbers.
- `launchctl print gui/$(id -u)/local.igdm` and `launchctl print gui/$(id -u)/local.igdm-dashboard`
  both show `state = running`.

Add `12-run-always` to `done` in `.setup-progress.json`, with the note
`"12-run-always": "running, first real DM not seen yet"`. Write it now, before you wait. If
Claude Code closes before the first real DM, this note is what makes the next session look for it
and celebrate.

### The first real DM

Now wait for the first real one, the DM to a real follower who commented their keyword. If
someone commented after `handle_comments_after`, it may already have gone out. Otherwise it's the
next person who comments.

- To see it, use "The first real DM check" in
  [../reference/progress.md](../reference/progress.md), in the form for where the bot runs (the
  server form on Windows or after a move). It counts first DMs in the bot's real database and
  prints a line like `real first DMs sent: 1`. Don't look for `DM 1 sent` in the
  log, because the live test and the dry runs wrote that line too. The Activity tab on the
  dashboard shows real DMs as well.
- If they'd like to wait, check every few minutes.
- If they need to go, that's fine. Tell them the dashboard and their DMs will show it, and that
  next time they open Claude Code here and say "continue setup", you'll look for it. Tell them
  how to come back, as in "Coming back to me" on the everyday card ([everyday.md](everyday.md)).
  The note already says `"running, first real DM not seen yet"`, so leave it. Then, on a Mac,
  offer chapter 13 now ("you can close your laptop and the bot keeps going"). If they say no, write the note
  `"13-cloud": "skipped for now"`. Show the everyday card ([everyday.md](everyday.md)) before they
  go.
- A later session that finds the "not seen yet" note runs the same check first (see
  [../SKILL.md](../SKILL.md), section 1). If the number is above 0, it celebrates in the words below and
  updates the note.

When the first real DM has gone out, this is the "it works!" moment. Celebrate it warmly. If there's a
coffee line in the quote below, say it this one time, exactly as written. Never say it again, in
this session or any other, and never in any other chapter. If there's no coffee line, say nothing
about coffee.

> It works! Your bot just sent its first real DM to a follower, all by itself, in your words.
> From now on it looks after everyone who comments your keyword, and you only need to check the
> "Needs you" part of the dashboard now and then.
>
> If this saved you time, you can buy Alex a coffee: https://buymeacoffee.com/alexwoodyai

Update the note to `"12-run-always": "running, first real DM celebrated"`, so it's never said
again. Then, on a Mac, offer chapter 13 ("you can close your laptop and the bot keeps going"),
unless it's already done or has a "skipped for now" note, and show the everyday card
([everyday.md](everyday.md)).

## If it doesn't work

- **`uv isn't installed`.** The script can't find uv. Run `command -v uv`, and if it's missing,
  go back to chapter 1, step 5.
- **`Bootstrap failed: 5: Input/output error`.** macOS hadn't finished stopping an older copy. Run
  `bash scripts/install_agent.sh` again.
- **`This Mac handed the bot to the server`.** The bot was moved to the cloud server earlier, so
  the Mac won't start it, which stops people getting two DMs. Ask them where they'd like it to run,
  and see chapter 13.
- **No `tick` line after two minutes.** Read `logs/launchd.err` and the end of `logs/igdm.log`.
  A `STARTUP FAILED` line says why, and the bot tries again by itself a few minutes later. Run
  `uv run igdm check` to see the same error straight away. A Meta error is in
  [../reference/meta-notes.md](../reference/meta-notes.md).
- **`Operation not permitted` in `logs/launchd.err`.** The project folder is somewhere macOS
  protects, like Desktop, Documents or Downloads. Go back to chapter 1, step 4, and move it.
- **The dashboard doesn't open.** Read `logs/dashboard.err`. Restart it with
  `launchctl kickstart -k gui/$(id -u)/local.igdm-dashboard`.
- **They changed `config.toml` and want the bot to use it now.** It reloads by itself within a
  minute. If it doesn't, restart both, with
  `launchctl kickstart -k gui/$(id -u)/local.igdm` and then
  `launchctl kickstart -k gui/$(id -u)/local.igdm-dashboard`.
- **"Instagram access is blocked" on the dashboard.** See "When Meta blocks access" in
  [everyday.md](everyday.md).
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot runs on the server, and nothing has been started there yet. This replaces steps 1 to 3.

1. **Say what's about to happen.** Say step 1 in your own words, but say "on your server" instead
   of "whenever your Mac is awake". The server is always on, so the bot runs day and night.
2. **Send the newest settings.** Run `bash scripts/push_config.sh`, in case anything changed since
   chapter 11. Check `messaging_node` is the one that worked in the live test.
3. **Start it.** Run `bash scripts/server_start.sh`. It looks at the server, runs `igdm check`
   there, starts the bot and the dashboard, and waits for the first pass. That can take a few
   minutes. It finishes with `The bot is running on your server. Dashboard: https://<tailscale name>`.
   - If it says the check failed, it started nothing. Read the lines above it, fix what they say,
     and run it again.
   - If it says the bot didn't start and it switched it off again, read the server's log with
     `ssh igdm 'journalctl -u igdm -n 50'` and follow [../reference/stuck.md](../reference/stuck.md).
4. **The dashboard tour.** Tailscale has to be switched on, on their computer, for the address to
   open. Open the `https://` address that `server_start.sh` printed, and do step 4 above.
5. **The Check on Windows.** `ssh igdm 'tail -n 20 app/logs/igdm.log'` shows a `tick` line from
   the last minute or two, `ssh igdm 'systemctl is-active igdm igdm-dashboard'` prints `active`
   twice, and the dashboard opens. Then add `12-run-always` to `done`, with the note
   `"12-run-always": "running, first real DM not seen yet"`, written now in case Claude Code
   closes. Wait for the first real DM as above, using the server form of "The first real DM
   check" in [../reference/progress.md](../reference/progress.md). After the celebration, update
   the note to `"12-run-always": "running, first real DM celebrated"` and show the everyday
   card. There's no chapter 13 to offer, because it's already done.
