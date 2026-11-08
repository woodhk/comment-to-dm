# Chapter 2: Instagram

## Why this matters

Instagram only lets programs like the bot read comments and send DMs for a professional account.
A professional account is a free Instagram setting for creators and businesses. It doesn't cost
anything, and they keep their followers, posts and username.

This chapter happens on their phone, in the Instagram app. You can't see their phone, so you give
one small step at a time, say what the screen should look like, and wait.

## Steps

Before you start, tell them it's a phone chapter:

> This part happens on your phone, in the Instagram app. I'll give you one small step at a time,
> and you tell me what you see. Meta moves its settings around now and then, so if something
> looks different, just tell me what's on your screen and we'll find it together.

1. **Check whether it's already professional.** Ask them to open Instagram and tap their profile
   picture at the bottom right. If they see "Professional dashboard" near the top of their
   profile, the account is already professional. Ask whether it's Creator or Business, then go
   to the note after step 6.

2. **Open the settings.** Ask them to tap the menu at the top right of their profile. It looks
   like three short lines. A page called "Settings and activity" opens with a long list.

3. **Find the account type.** Ask them to scroll down to the part for professionals and tap
   "Account type and tools". If they can't see it, they can use the search bar at the top of
   "Settings and activity" and type "account type".

4. **Start the switch.** Ask them to tap "Switch to professional account". Instagram shows a few
   screens that explain what a professional account is. They tap Continue through them.

5. **Pick a category.** Instagram asks what best describes them, like "Digital creator" or
   "Education". Any category is fine for the bot. It can show it on their profile, and there's
   usually a switch to hide it.

6. **Creator or Business.** Instagram asks which kind of professional account they want.
   - Tell them either works for the bot. If they're unsure, pick Creator, because it fits a
     person who makes content.
   - Before they confirm, tell them that a professional account has to be public. If their
     account is private now, Instagram will make it public.
   - Instagram may offer to connect a Facebook Page or set up contact details along the way. Tell
     them they can skip those screens. Chapter 3 sorts out the Facebook Page properly.

Then tell them one more thing in a sentence or two. Some Instagram guides mention an "Allow
access to messages" switch. Many people never find it, and the bot's DMs work without it, so they
don't need to look for it now. Only come back to it if DMs fail in the live test (chapter 11). It
has been in either of these places.

- Settings, then Messages and story replies, then Message controls, then Connected tools.
- Settings, then Creator tools and controls (or Business tools and controls), then Connected
  tools.

## Check

Ask them to tap their profile picture again. They should see "Professional dashboard" near the
top of their profile. Ask them to tell you their Instagram username, and whether they picked
Creator or Business. The username isn't a secret, and you'll look for it in chapter 3.

When it passes, add `02-instagram` to `done` in `.setup-progress.json`, with a note like
`"02-instagram": "Creator account, @theirname"` (see
[../reference/progress.md](../reference/progress.md)). Tell them their Instagram is ready for the
bot, then go to chapter 3.

## If it doesn't work

- **They can't find "Account type and tools".** Ask them to read out the headings they see on
  "Settings and activity", and work from those. The search bar at the top of that page usually
  finds it with "professional" or "account type".
- **"Switch to professional account" isn't there, but "Switch to personal account" is.** The
  account is already professional. Go to the Check.
- **They have more than one Instagram account on the phone.** Ask them to check the username at
  the top of the profile is the account the bot is for, before they switch.
- **Instagram shows an error or won't finish the switch.** Ask them to close the app fully, open
  it again and try once more. If it still fails, follow
  [../reference/stuck.md](../reference/stuck.md).
