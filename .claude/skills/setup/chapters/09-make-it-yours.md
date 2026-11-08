# Chapter 9: Make it yours

## Why this matters

Everything the bot sends goes out in the creator's name. The DMs, the short public replies, the
email and the AI replies should all sound like them, and say exactly what they're giving away.
This chapter is a conversation. You ask, they answer, and you write their settings file
(`config.toml`) for them. Then you read every wording back before any of it goes out.

## Steps

Ask one question at a time, in plain words, and wait for each answer. Write each answer into
`config.toml` as you go, and say briefly what you changed. Read
[../reference/voice.md](../reference/voice.md) again first. Rule 10 is the heart of this chapter.

Before you start, read `config.example.toml` so you know every line and its comment. Change only
lines that already exist there, keep their spelling exactly, and keep TOML's quotes. Text goes
in `"…"`, and text over several lines goes in `"""…"""`. If a reel link, keyword, offer and
`dm_offer` were added in chapter 7 (see the note on `09-make-it-yours`), don't ask for them again.
Read them back and ask if they're still right. If the note has no `dm_offer`, ask for it in step 5.

1. **Their name.** Ask how their name should appear to people, for example "Sam" or "Sam Lee".
   - Set `[owner] name`, and `[email] from_name`, which is the name people see on the email.
   - The email bodies in `config.toml` end with `Your name`. Change that line to their name, in
     the `[messages]` email and in the reel's email.

2. **A few words about them.** Ask what they post about, in one line, like "a personal trainer
   who posts quick home workouts". Set `[owner] about`. The AI reads it, so it knows who it's
   writing as.

3. **The reel.** Ask for the link to the reel they want the bot to work on first. In the
   Instagram app they tap the reel, then the share arrow, then "Copy link". Drop anything after a
   `?`. Set `url` in the `[[reels]]` block. Leave `key = "first"` as it is. The key is only a short
   name the bot uses inside.

4. **The keyword.** Ask which word people comment to get the material. It's usually the word the
   reel asks for, like "Comment GUIDE and I'll send it". Set `keyword`.

5. **What they're giving away.** Ask for two versions.
   - In full, for the AI, like "my free 5-day meal plan with a shopping list". Set `offer`.
   - In a few words, to finish the first DM, like "the meal plan". The first DM reads "just drop
     your email and i'll send you the meal plan". Set `dm_offer`.

6. **The material link.** Ask for the link people get in the email. It can be Google Drive,
   Dropbox, or their own website.
   - If it isn't online yet, offer to help them put it in Google Drive. They upload the file at
     https://drive.google.com (the upload and the login are theirs).
   - For a Drive file or folder, they need to share it so anyone can open it. In Drive they
     right-click the file and choose "Share", then "Share" again. Under "General access" they
     change "Restricted" to "Anyone with the link", leave it as "Viewer", and click "Copy link",
     then "Done".
   - Ask them to check it opens for a stranger. They open a private window in Chrome (press
     Command, Shift and N on a Mac, or Control, Shift and N on Windows), paste the link and press
     Enter. If it asks them to sign in or request access, the sharing isn't open yet.
   - Set `material_url`.

7. **The email.** Show them the current email subject and body from the reel's block, with the
   blanks filled in for this reel, so they can picture it. `{name}` becomes the person's name,
   `{keyword}` the keyword and `{material_url}` the link.
   - Ask if they'd like to write their own, or if they'd like you to draft one in their voice.
     If you draft it, keep it short, warm and plain, sign it with their name, and keep
     `{material_url}` on its own line. Then read it back and change it until they're happy.
   - Set the reel's `email_subject` and `email_body`. Keep `{name}`, `{keyword}` and
     `{material_url}` exactly as written, curly brackets and all. Those are the only blanks the
     email can use.

8. **People who already commented.** Ask:

   > Some people may have commented on this reel already. Would you like the bot to DM them too,
   > or only people who comment from now on?

   - **From now on.** Set `handle_comments_after` to the time right now in their timezone. Get it
     with `TZ="<their timezone>" date +%Y-%m-%dT%H:%M:%S`, using the `timezone` from
     `config.toml`. It looks like `handle_comments_after = "2026-11-09T14:30:00"`.
   - **Them too.** Explain that Instagram only lets the bot answer a comment privately for about 7
     days, so older comments can't get a DM. Ask how far back they'd like, up to 7 days, and set
     `handle_comments_after` to that time. Tell them those people get their DMs when the bot
     starts running for real in chapter 12, a few minutes apart, not during the tests.

9. **How they text.** The AI writes the odd reply when someone asks a question, and it should
   sound like them. Ask which they'd prefer.
   - **A few plain questions.** Do they write in lowercase or with capitals? Do they use emojis,
     and which ones? Any words they say a lot, like "hey", "sweet" or "no worries"? Short and
     casual, or a bit more polished?
   - **Real examples.** They paste two or three DMs they've sent to followers. Tell them to pick
     ones with no private details in them.
   - From their answers, write `[owner] style` as one line, like
     `style = "lowercase, short and friendly, says 'hey' and 'no worries', the odd 🙌, never formal."`.
     Read it back and ask if it sounds like them.

10. **The DM wording.** Go through `[messages]` with them, one group at a time. Show each wording
    the way a follower would see it, with the blanks filled in. Ask if it sounds like them, and
    change anything that doesn't.
    - `dm1` is the first DM. By default it asks for the email straight away
      (`ask_email_first = true`). If they'd rather ask "do you still want it?" first, set
      `ask_email_first = false` and use a `dm1` like the one in the comment above it. `dm1` and
      `repeat_dm1` can only use `{name}`, `{keyword}` and `{offer}`.
    - `repeat_dm1` goes to someone the bot already has an email for, from an earlier reel.
    - `followup_1` and `followup_2` go to someone who says yes before giving an email.
    - `public_replies` are the short replies under the comment, picked at random. Read a handful
      aloud, and take out any they'd never say.
    - `got_it` is sent when they give their email, and `sent_dm` once the email has gone out.
      `sent_dm` can only use `{email}`.
    - Also change the `[messages]` `email_subject` and `email_body` to match the reel's email, so
      any new reel without its own email still sounds like them.

11. **The prewritten replies.** Show them each `[[replies.bank]]` entry. Say in a sentence when
    it's used (from its `when` line), then show its `replies`. Change the `replies` wording until
    it sounds like them. Leave `id`, `stage` and `when` exactly as they are, because they steer
    how the checker picks a reply. The `yes` entry has no wording of its own. It uses
    `followup_1` and `followup_2`.

12. **AI replies on or off.** `[replies] enabled` stays `false` until they've read all the
    wording in steps 10 and 11. Then ask:

    > Now that you've read the wording, would you like the bot to answer people who write back
    > with a question? It uses the replies we just went through, and the AI writes one only when
    > none of them fits. Anything it isn't sure about comes to you instead.

    If they say yes, set `[replies] enabled = true`. If they'd rather wait, leave it `false` and
    tell them those messages will come to them on the dashboard instead.

## Check

Run this. It loads the settings file the same way the bot does.

```
uv run python -c "from igdm.config import load_config; load_config('config.toml')"
```

It prints nothing when the file is good. An error names the line or setting that's wrong. Fix it
and run it again.

Then read back a short summary, in a few lines. Say their name, the reel, the keyword, what
they're giving away, the material link, from when comments are handled, and whether AI replies
are on. Ask them to confirm it all.

Add `09-make-it-yours` to `done` in `.setup-progress.json`, with a note like
`"09-make-it-yours": "reel GUIDE, comments from 2026-11-09 14:30, AI replies on"`. Then go to
chapter 10.

## If it doesn't work

- **`bad reel url (no shortcode)`.** The link isn't a reel or post link. It should look like
  `https://www.instagram.com/reel/ABC123xyz/`. Ask them to copy it again from the app.
- **`bad handle_comments_after in reel 'first'`.** The time must look like
  `"2026-11-09T14:30:00"`, in quotes, with a `T` between the date and the time.
- **`missing 'offer' in reel 'first'`, or another `missing` line.** A line was deleted. Put it
  back from `config.example.toml`. If `dm_offer` is missing, the bot doesn't complain, but the
  first DM says "the material" instead of their own words, so put it back too.
- **`messages.dm1 can only use {name}, {keyword} and {offer} in curly brackets`.** A blank is
  misspelled, or there's a stray curly bracket in the DM. Fix the blank, or take the bracket out.
- **`messages.sent_dm can only use {email} in curly brackets`.** The same, for `sent_dm`.
- **`[[replies.bank]] … needs at least one reply wording`.** A `replies` list was emptied. Every
  entry except `yes` needs at least one wording.
- **An error about `Expected` or `Invalid` with a line number.** That's a TOML typing slip, like
  a missing quote or a quote inside the text. A quote inside text needs `\"`, or use `"""…"""`.
  Look at the line it names.
- **The material link asks people to sign in.** The sharing isn't "Anyone with the link" yet. Go
  back to step 6.
- Anything else, follow [../reference/stuck.md](../reference/stuck.md).

## On Windows

The settings file on their computer is the copy you edit. The bot reads the server's copy.

- Make every change in the local `config.toml`, exactly as above.
- For the time in step 8, ask the server, which already runs in their timezone:
  `ssh igdm 'date +%Y-%m-%dT%H:%M:%S'`.
- There's no uv on the Windows computer, so the Check is different. Run
  `bash scripts/push_config.sh`. It sends the file to the server and loads it there the same way
  the bot does, before it replaces the old one. If the file has a mistake, it says "The new
  settings didn't pass the check, so the server keeps its old ones." and shows the error. Fix it
  locally and run it again.
- When it works, it prints `pushed config.toml` and says the bot and the dashboard are
  `inactive`. That's normal, because nothing is started until chapter 12.
