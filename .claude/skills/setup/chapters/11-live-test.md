# Chapter 11: The live test

## Why this matters

This is the first time the bot sends real messages, so it's done in a way that can't reach a real
follower. Three things keep followers out of it.

- **A second Instagram account** does the commenting and replying, so the only person the bot
  messages is them (or a friend who agreed).
- **A quiet reel**, an older one that hardly gets comments any more, so no follower comments in
  the middle of the test.
- **A separate test database.** A database is the file where the bot keeps its records, like who
  it has messaged. The test uses its own one, `data/livetest.sqlite`, so the real one starts clean
  in chapter 12. And `handle_comments_after` is set to the time the test starts, so every older
  comment on the quiet reel is ignored.

Say why in a sentence or two when you start, for example "This keeps your real followers out of
the test."

## Steps

Do these one at a time. You make the test settings, run the commands and read the log. They do
everything in the Instagram app.

1. **The second account.** Ask them for a second Instagram account they can use on their phone
   or in a browser, like a spare one of theirs or a friend's.
   - It must have no role on their Meta app, so not an admin, developer or tester. An account they
     never added in chapter 4 has no role, which is what you want. This proves the bot can DM
     ordinary people.
   - Ideally it doesn't follow them. Then its replies land in their Message Requests folder, and
     the test shows the bot still sees them.
   - If they have quiet hours set in `config.toml` (`quiet_hours` under `[pacing]`, off by
     default), do the test outside them, because quiet hours block every message.

2. **The quiet reel.** Ask for the link to an older reel of theirs that hardly gets comments now.
   In the Instagram app they tap the reel, then the share arrow, then "Copy link". Drop anything
   after a `?`. It must not be the reel from chapter 9, because a real follower commenting there
   during the test would get a DM now, and the real one doesn't know about it.
   - **No quiet reel.** If every reel still gets comments, offer a throwaway post. They post
     something new just for the test, like a plain photo or a short reel, and archive or delete
     it once the test is done. Say this is the safe option, because no follower has a reason to
     comment on a post like that in the next half hour. Use its link as the quiet reel, and write
     a note, `"11-live-test": "using a throwaway test post, archive it after"`.
   - **They'd rather use the real reel anyway.** Say plainly what could happen. A follower who
     comments during the test gets a DM from the test, and the real database never hears about
     it, so once the bot starts for real, that follower would get the same DM a second time. If
     they still want to, keep the real reel's link in step 3. Then, when the test is done and
     before chapter 12, set `handle_comments_after` in the real `config.toml` to the time the test
     ended, so nobody who commented during the test is DMed again. Also run
     `grep "DM 1 sent" logs/igdm.log | tail -n 20` at the end, and tell them about any username
     that isn't the second account, so they can finish with that person by hand. Write a note,
     `"11-live-test": "used the real reel, set handle_comments_after to the end of the test"`.

3. **The test settings.** Run `cp config.toml config.test.toml`, then change these five things in
   `config.test.toml` only. Leave everything else as it is.
   1. `db_path = "data/igdm.sqlite"` becomes `db_path = "data/livetest.sqlite"`. This keeps the
      test out of the real database.
   2. Under `[pacing]`, `dm_delay_seconds = [50, 70]` becomes `dm_delay_seconds = [0, 0]`, so the
      first DM goes out straight away instead of a minute later.
   3. In the `[[reels]]` block, `url` becomes the quiet reel's link, in quotes.
   4. In the same block, `handle_comments_after` becomes the time right now in their timezone. Get
      it with `TZ="<their timezone>" date +%Y-%m-%dT%H:%M:%S`, using the `timezone` from
      `config.toml`, for example `handle_comments_after = "2026-11-09T15:20:00"`. Every comment
      before this time is ignored, so only the test comment is handled.

   5. Keep exactly one `[[reels]]` block, the quiet reel's. Delete any other `[[reels]]` blocks,
      and delete any `hand_dm_phrases` line in it. Another reel in the test copy would let a real
      follower's comment there get a test DM, and old hand-DM phrases could make the bot step back
      from the test chat.

   Then check it loads, with
   `uv run python -c "from igdm.config import load_config; load_config('config.test.toml')"`.
   It prints nothing when it's good. Tell them in a sentence what you changed and why.

4. **The test comment.** Ask them to comment their keyword on the quiet reel, from the second
   account. It has to be the second account, because the bot never answers their own comments.
   Ask them to tell you when it's posted.

5. **The first pass.** Run `uv run igdm --config config.test.toml once`. Every command in this
   chapter has `--config config.test.toml`, so it uses the test settings. Read the log lines.
   - `comment … by @<second account> -> queued` means it saw the comment and decided it's a
     request.
   - `DM 1 sent for comment … to @<second account>` means the first DM went out.
   - Ask them to look at the second account's DMs. The first DM should be there, and a short reply
     should sit under the comment on the reel.

6. **Celebrate the first test DM.** When it arrives, say so warmly, in a sentence or two:

   > Your bot just sent its first DM! That message came from your own Instagram, written in your
   > words, all by itself.

7. **The second pass.** Wait about a minute, then run `uv run igdm --config config.test.toml once`
   again, and `uv run igdm --config config.test.toml status`.
   - With `ask_email_first = true` (the usual), status should show `awaiting_email: 1`. With
     `ask_email_first = false`, it should show `awaiting_yes: 1`.
   - If it shows `handled_by_owner` instead, under "bot stopped because you replied by hand", the
     bot mistook its own DM for one they typed. Stop and see "If it doesn't work".

8. **The "yes" step.** Only if `ask_email_first = false`. Ask them to reply "yes" from the second
   account. Wait about 90 seconds (the bot waits that long in case someone is still typing), then
   run `uv run igdm --config config.test.toml once`. The two follow-up messages, `followup_1` and
   `followup_2`, should arrive.

9. **A question, if AI replies are on.** Only if `[replies] enabled = true`, and it's optional.
   Do it before the email step, while the bot is still waiting for an email.
   - Ask them to reply "is it free?" from the second account. Wait about 90 seconds and run
     `uv run igdm --config config.test.toml once`. The answer should be one of their prewritten
     `free` replies.
   - If they'd like to see the AI write one, ask them to reply with a question none of the
     prewritten replies cover, like "how long is it?". Wait about 90 seconds and run `once`
     again. The AI writes a short answer in their style, which uses the AI key from chapter 7.
     Read it with them. If it doesn't sound like them, improve `[owner] style` afterwards.

10. **The email step.** Ask them to reply from the second account with an email address they can
    check, like their own. Wait about 90 seconds, run `uv run igdm --config config.test.toml once`,
    and a "got it" DM should arrive. Wait about 30 seconds and run it once more. The email goes out
    from their Gmail, and a last DM says it's been sent.
    - Ask them to check that inbox, including spam. The email should have their subject, their
      words and the material link.
    - Run `uv run igdm --config config.test.toml emails`. It should list that address.

11. **The checklist.** Go through these with them, one at a time. If one fails, stop, read the
    matching lines in `logs/igdm.log`, and fix it before going on (see
    [../reference/stuck.md](../reference/stuck.md)).
    1. **The account is right.** `uv run igdm check` shows `api: facebook_login`, their Page id,
       their Instagram username and the same Instagram id that `igdm connect` printed.
    2. **DM 1 went out.** The second account got the first DM. If it didn't, see "If DM 1 fails"
       below.
    3. **The bot knows its own messages.** Status showed `awaiting_email: 1` (or `awaiting_yes: 1`),
       not `handled_by_owner` (step 7).
    4. **Replies reach the right conversation.** The "got it" (and the follow-ups, if they had the
       yes step) arrived after the second account replied.
    5. **Every message used the same way to send.** The follow-ups, the "got it" and the last DM
       all arrived, just like DM 1.
    6. **The words are exactly theirs.** They open the conversation in the Instagram app and
       compare it with `config.toml`. Nothing is cut off or changed.
    7. **Replies from people who don't follow them are seen.** If the second account doesn't
       follow them, its replies went to their Message Requests, and the bot still answered.
    8. **No message errors.** Run `grep -E "failed|ERROR" logs/igdm.log | tail -n 20`. There should
       be nothing about listing conversations or messages from the test.
    9. **Comments show who wrote them.** The `comment … by @… -> queued` line shows the second
       account's username, not a blank `@`.
    10. **The public reply sits under the comment.** On the reel it's an indented reply to the test
        comment, not a new comment of its own.
    11. **DMs to an ordinary account work.** The second account has no role on the app, and the DM
        arrived anyway.
    12. **The name in the DM.** The first DM may use the Instagram handle instead of a first name.
        That's normal, because Instagram only shares the name once the person has messaged them.
        It must not cause an error.
    13. **No token refresh.** `uv run igdm refresh-token` prints that it's `not needed`, because
        the Page token from chapter 5 never expires.
    14. **The keys file is private.** `ls -l .env` starts with `-rw-------`, which means only their
        own user on this computer can read it. This only looks at the file's permissions, never
        inside it.
    15. **The checker makes sense.** In `uv run igdm check`, the two `should be high` samples
        scored above the threshold (0.8 unless changed), and the `should be low` one scored low.
    16. **The clock is right (Mac).** In System Settings, then General, then Date & Time, "Set time
        and date automatically" is on.

### If DM 1 fails

If DM 1 fails with an error about an unsupported endpoint or a permission, switch the way the bot
sends DMs. By default it sends them through their Facebook Page (`messaging_node = "page"`).
Meta's own documents disagree on this, so the other way, through the Instagram account, sometimes
works instead.

1. Set `messaging_node = "ig"` in both `config.toml` and `config.test.toml`.
2. Try again from scratch, as in "Trying again" below.
3. Write which one worked in a note, like `"11-live-test": "DMs work with messaging_node = ig"`.

If it still fails with `"ig"`, follow [../reference/stuck.md](../reference/stuck.md) and
[../reference/meta-notes.md](../reference/meta-notes.md). Put `messaging_node = "page"` back if
neither worked, so the setting matches what Meta's notes expect.

### Trying again

To run the test again from the start, delete the test database, set the time again, and comment
again.

1. `rm -f data/livetest.sqlite data/livetest.sqlite-wal data/livetest.sqlite-shm`
2. Set `handle_comments_after` in `config.test.toml` to the time now (step 3).
3. They comment the keyword again from the second account, and you go on from step 5.

### Clean up

When the checklist is done, remove the test settings and the test database. The real settings
and the real database aren't touched.

```
rm -f config.test.toml data/livetest.sqlite data/livetest.sqlite-wal data/livetest.sqlite-shm
```

If `messaging_node = "ig"` was what worked, check it's still in `config.toml`.

If they used a throwaway test post, remind them to archive or delete it in the Instagram app now.
If they used the real reel, set `handle_comments_after` in `config.toml` to the time now, as in
step 2.

## Check

- The second account got the first DM, the "got it", and the email with the material link.
- `uv run igdm --config config.test.toml emails` listed the test address (before the clean up).
- Every checklist item passed.
- After the clean up, `ls config.test.toml data/livetest.sqlite` says both are gone ("No such
  file or directory" for each). On Windows, also run
  `ssh igdm 'cd app && ls config.test.toml data/livetest.sqlite'`, which should say the same on
  the server.

Add `11-live-test` to `done` in `.setup-progress.json`, with a note like
`"11-live-test": "test DM, got it and email all worked, messaging_node = page"`. Then go to
chapter 12.

## If it doesn't work

- **No `comment …` line at all.** Check the comment came from the second account, that it was
  posted after the `handle_comments_after` time, and that the `url` is the quiet reel. If all
  three are right, Instagram may be holding the comment back. Compare the reel's comment count in
  the app with what `uv run igdm --config config.test.toml check` shows. Wait a minute and run
  `once` again.
- **`-> left_for_owner` for the test comment.** The checker didn't see a request. Ask them to
  comment just the keyword, and try again (see "Trying again").
- **DM 1 fails with an unsupported endpoint or permission error.** See "If DM 1 fails" above.
- **Status shows `handled_by_owner` after DM 1.** The bot didn't recognise its own message. Run
  `uv run igdm check` and compare the `account:` line with what `igdm connect` printed in chapter
  5. Copy the matching lines from `logs/igdm.log` and follow
  [../reference/stuck.md](../reference/stuck.md).
- **Nothing happens after "yes" or the email.** Wait a full 90 seconds after their reply before
  `once`, then try once more. If still nothing, look in `logs/igdm.log` for lines about
  conversations or messages.
- **The "got it" came but no email.** Run `once` again after 30 seconds. If it still doesn't send,
  run `uv run igdm test-gmail` (chapter 8) and read the log line about the email.
- **`already running: another igdm is sending`.** Another copy of the bot is running. On a Mac,
  check nothing from chapter 12 is installed yet, with
  `launchctl print gui/$(id -u)/local.igdm`. If it is, that's fine, but stop and ask them before
  testing, because the real bot is live.
- **`code=190` or `code=200`.** That's Meta. See
  [../reference/meta-notes.md](../reference/meta-notes.md).
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The bot runs on the server, so the test settings go there and every command runs there. Make the
test settings on their computer first, then copy them across.

- Make `config.test.toml` locally with `cp config.toml config.test.toml`, and make the same five
  changes as in step 3, including one `[[reels]]` block only, with no `hand_dm_phrases`, `db_path = "data/livetest.sqlite"` and
  `dm_delay_seconds = [0, 0]`. For `handle_comments_after`, get the time from the server, which
  already runs in their timezone, with `ssh igdm 'date +%Y-%m-%dT%H:%M:%S'`.
- Copy it to the server with `scp config.test.toml igdm:app/`. Check it loads there with
  `ssh igdm "cd app && ~/.local/bin/uv run python -c \"from igdm.config import load_config; load_config('config.test.toml')\""`.
- Run each command over SSH, in this form.
  - `ssh igdm 'cd app && ~/.local/bin/uv run igdm --config config.test.toml once'`
  - `ssh igdm 'cd app && ~/.local/bin/uv run igdm --config config.test.toml status'`
  - `ssh igdm 'cd app && ~/.local/bin/uv run igdm --config config.test.toml emails'`
- The log is on the server. Read it with `ssh igdm 'tail -n 50 app/logs/igdm.log'`, and for item
  8 run `ssh igdm 'grep -E "failed|ERROR" app/logs/igdm.log | tail -n 20'`.
- Item 13 is `ssh igdm 'cd app && ~/.local/bin/uv run igdm refresh-token'`, and item 14 is
  `ssh igdm 'ls -l app/.env'`. Skip item 16, because the server keeps its own clock right.
- **If DM 1 fails.** Set `messaging_node = "ig"` in the local `config.toml` and `config.test.toml`.
  Send the first with `bash scripts/push_config.sh` and the second with
  `scp config.test.toml igdm:app/`.
- **Trying again.** Delete the test database with
  `ssh igdm 'cd app && rm -f data/livetest.sqlite data/livetest.sqlite-wal data/livetest.sqlite-shm'`,
  set the time again locally and copy the file across again.
- **The real reel, if they chose it in step 2.** Look for other people's DMs with
  `ssh igdm 'grep "DM 1 sent" app/logs/igdm.log | tail -n 20'`. Get the end time from the server
  with `ssh igdm 'date +%Y-%m-%dT%H:%M:%S'`, set it as `handle_comments_after` in the local
  `config.toml`, and send it with `bash scripts/push_config.sh`.
- **Clean up** on both computers, with `rm -f config.test.toml` locally and
  `ssh igdm 'cd app && rm -f config.test.toml data/livetest.sqlite data/livetest.sqlite-wal data/livetest.sqlite-shm'`.
- The server's bot isn't started until chapter 12, so `already running` shouldn't appear. If it
  does, run `ssh igdm 'systemctl is-active igdm'` and stop to ask them before testing.
