# Chapter 10: Check and dry run

## Why this matters

Everything is in place now, so before anything is sent, you look at what the bot would do. First
`igdm check` makes sure the keys, the reel and the checker all work together with their real
settings. Then a dry run goes through the real comments on their reel exactly as the bot would,
but sends nothing and posts nothing. A dry run is a practice pass that only writes down what it
would have done.

The dry run keeps its own records in `data/dryrun.sqlite`, separate from the bot's real ones, so
it never changes who gets a DM later.

## Steps

1. **Say what's coming.** Tell them:

   > Now we'll do a practice run on your real reel. The bot reads the comments and decides who's
   > asking for your material, but it doesn't send a single message or post anything. Then I'll
   > tell you in plain words what it would have done.

2. **The check.** Run `uv run igdm check`. You ran it in chapter 7, and now it uses their real
   keyword and offer. Go through the lines as in chapter 7, briefly. The two lines that matter most
   are `account: @…`, which should be their username, and the three `sample` lines, which should
   have no `<- not as expected`. If anything fails, fix it before the dry run (see "If it doesn't
   work").

3. **Start the dry run fresh.** An earlier dry run's records would hide comments it already saw,
   so clear them first. This only removes practice records.

   ```
   rm -f data/dryrun.sqlite data/dryrun.sqlite-wal data/dryrun.sqlite-shm
   ```

4. **Run it.** Run `uv run igdm once --dry-run`. It does one pass, the same as the bot does each
   minute.
   - It prints the bot's log lines, then one line that counts what each part did, like
     `{'pauses': 0, 'ingest': 4, 'dm': 1, …}`. `ingest` is how many new comments it read, and `dm`
     is how many first DMs it would have sent.
   - The bot sends at most one first DM per pass, to keep a natural pace. After each one it also
     waits a minute or more (`backlog_gap_seconds`) before the next, even in a dry run, and a
     comment from the last minute isn't due yet (`dm_delay_seconds`). So `'dm': 0` on a later
     pass doesn't mean nobody is left. It usually means the next one isn't due yet. The `queued`
     log lines show who is waiting. To see the next DM, wait a couple of minutes and run
     `uv run igdm once --dry-run` again, without clearing the records.
   - It does ask the checker about each new comment, so it may use a fraction of a cent of AI.

5. **If there's nothing to see.** If they chose "only people who comment from now on" in chapter
   9, there may be no comments after that time yet, so the dry run reads them all and skips them.
   That's correct, and it's also not much of a practice. Offer to look back a week, in a copy of
   the settings that only the dry run uses.
   - Run `cp config.toml config.dryrun.toml`.
   - In `config.dryrun.toml`, set the reel's `handle_comments_after` to 7 days ago in their
     timezone. Get it with `TZ="<their timezone>" date -v-7d +%Y-%m-%dT%H:%M:%S`.
   - In the same copy, under `[pacing]`, set `backlog_gap_seconds = [0, 0]` and
     `dm_delay_seconds = [0, 0]`, so each pass shows the next DM straight away.
   - Clear the practice records (step 3), then run
     `uv run igdm --config config.dryrun.toml once --dry-run`, more than once if several people
     are waiting.
   - Then run `rm -f config.dryrun.toml`. Their real settings never changed.

6. **Explain what it would have done.** Read the log lines and tell them in plain words, person
   by person. Use their usernames, and keep it short.
   - `comment … by @sam -> queued` means Sam asked for the material, and the bot would DM them.
   - `comment … by @sam -> left_for_owner` means the checker decided it wasn't a request, so the
     bot leaves it alone.
   - `-> duplicate` means that person already asked, so they don't get a second DM.
   - `-> expired` means the comment is older than about 7 days, which is too old for Instagram to
     allow a private reply.
   - A comment from before `handle_comments_after` gets no `comment …` line at all. The bot saves
     it as `pre_cutoff`, which means "before the start time", and leaves it alone. It still
     counts in `ingest`. So `'ingest': 4` with no `comment …` lines means all four came before the
     start time they picked in chapter 9. That's the bot doing what they asked, not a fault. Step
     5 shows how to look back a week if they'd like a fuller practice.
   - `DM 1 sent for comment … to @sam`, then `DRY-RUN would send private reply to comment …: '…'`,
     is the first DM it would have sent, word for word. Read the wording to them.
   - `DRY-RUN would send public reply to comment …: '…'` is the short reply it would have posted
     under the comment.
   - If a decision looks wrong to them (someone who clearly asked was left alone, or the other way
     round), talk it through. A clearer keyword or a fuller `offer` usually fixes it. Change it in
     `config.toml` with their OK, and run the dry run again from step 3.

## Check

- `uv run igdm check` has no `ERROR` and no `<- not as expected`.
- The dry run finished, and its last line has no part that says `error:`. A part that failed
  makes the command exit with code 1.
- They've heard what it would have done, and they're happy with it. If there were no comments to
  read even a week back, that's fine. Tell them the live test in the next chapter shows the real
  thing.

Tell them it looks good in a sentence. Add `10-check-and-dry-run` to `done` in
`.setup-progress.json`. Then go to chapter 11.

## If it doesn't work

- **`error: reel(s) did not resolve to a media id: first`.** The bot can't find the reel on their
  account. Check the link in `config.toml` against the app, and that the reel is on the Instagram
  account the bot is connected to (`igdm check` shows which).
- **A part says `error:` in the last line, like `'ingest': 'error: …'`.** Read the matching
  `phase … failed` line in the log above it. A `code=190` or `code=200` is Meta, so see
  [../reference/meta-notes.md](../reference/meta-notes.md). An `openai` or `jev` error is the AI
  key, so see chapter 7.
- **`comment … by @ -> …` with nothing after the `@`.** Instagram didn't share the username. Check
  that "Business Asset User Profile Access" was added in chapter 4.
- **Someone who clearly asked shows `left_for_owner`.** See step 6. A short keyword that's also a
  normal word, or an empty `offer`, is the usual cause.
- **A comment they can see on Instagram doesn't show up at all.** Instagram sometimes holds
  comments back from programs. Compare the reel's comment count in the app with the
  `reel first: … N comments` line from `igdm check` before blaming the bot.
- **`date: illegal option -- v`.** That `date` isn't the Mac's. Use
  `TZ="<their timezone>" date -d '7 days ago' +%Y-%m-%dT%H:%M:%S` instead.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot runs on the server, so both commands run there, and the practice records live there.

- The check is `ssh igdm 'cd app && ~/.local/bin/uv run igdm check'`.
- Clear the practice records with
  `ssh igdm 'cd app && rm -f data/dryrun.sqlite data/dryrun.sqlite-wal data/dryrun.sqlite-shm'`.
- The dry run is `ssh igdm 'cd app && ~/.local/bin/uv run igdm once --dry-run'`.
- For the look-back in step 5, make `config.dryrun.toml` locally with
  `cp config.toml config.dryrun.toml`, make the same changes as in step 5, and get the time from the server with
  `ssh igdm "date -d '7 days ago' +%Y-%m-%dT%H:%M:%S"`. The server already runs in their
  timezone. Send the copy with `scp config.dryrun.toml igdm:app/`, then run
  `ssh igdm 'cd app && ~/.local/bin/uv run igdm --config config.dryrun.toml once --dry-run'`.
  Afterwards remove both copies, with `rm -f config.dryrun.toml` and
  `ssh igdm 'cd app && rm -f config.dryrun.toml'`.
- Before the first command, make sure the server has their newest settings from chapter 9. If in
  doubt, run `bash scripts/push_config.sh` again.
