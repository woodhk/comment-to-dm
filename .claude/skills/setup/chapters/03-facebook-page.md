# Chapter 3: Facebook Page

## Why this matters

Meta reaches an Instagram account through the Facebook Page it's linked to. A Facebook Page is a
public page for a person or a brand, separate from their personal Facebook profile. The bot talks
to Instagram by way of that Page, so the Page and the Instagram account must be linked, and the
Facebook account they use must be an admin of the Page. An admin is someone with full control of
the Page.

This is the step people most often get stuck on later, so it's worth checking carefully now.

## Steps

Do these one at a time, and wait after each.

1. **VPN off.** A VPN is an app that sends your internet through another country. Ask whether
   they use one. If they do, ask them to switch it off for this chapter and the next two. Say why
   in a sentence:

   > Facebook sometimes refuses changes when a VPN is on, and it can take ten minutes or so to
   > calm down after you switch it off. So it's easiest to keep it off until we're connected.

   On Windows, Tailscale is already on from chapter 13. Add this, so they don't switch it off too:

   > Tailscale doesn't count. Keep it on, because it's how I reach your server. Only a VPN app
   > that changes your country needs to go off.

2. **The right Facebook account.** Ask which Facebook account manages their Page, or will manage
   it if they don't have a Page yet. Tell them it matters:

   > Please use the Facebook account that's an admin of your Page. Meta only lets the bot in
   > through an account that has full control of the Page.

   If they have more than one Facebook account, make sure they use that one for the rest of the
   Meta chapters.

3. **Log in to Facebook.** Warn them about the Chrome permission first (see
   [../reference/chrome.md](../reference/chrome.md)), then open https://www.facebook.com in
   Chrome. They log in themselves, including any 2FA code. Wait until they say they're in.

4. **Open Business settings.** Open https://business.facebook.com/latest/settings. This is Meta
   Business Suite, Meta's free tool for managing Pages and Instagram accounts. Warn them first
   that it may ask permission for business.facebook.com.
   - If it asks them to log in again or choose an account, that's theirs.
   - If it asks them to set something up before it shows the settings, read the page and tell
     them what it asks. If it wants a business portfolio made, try the Page's own settings
     instead (see "If it doesn't work").

5. **Find their Page.** In the left menu, click "Business assets", then "Pages". Read the list.
   - **Their Page is there.** Click it. A panel opens with the tabs "People", "Partners" and
     "Connected assets". Go to step 7.
   - **No Page at all.** Go to step 6.
   - **Several Pages.** Ask which one is for this Instagram account.

6. **Make a simple Page (only if they have none).** Tell them a Page takes a couple of minutes and
   it's free. Open https://www.facebook.com/pages/create. With their OK, fill in the Page name
   (usually their creator name) and a category like "Digital creator". The rest is optional. They
   click Create Page themselves, and can skip the extra screens about photos and invites.
   Write a note, `"03-facebook-page": "made a new Page, still to link Instagram"`, so a second
   session doesn't make another one. Then go back to step 4 and find the new Page. If it isn't
   listed there, link Instagram from the Page's own settings (see "If it doesn't work").

7. **Look at Connected assets.** On the Page's panel, click the "Connected assets" tab. Read it.
   - If their Instagram username is listed, the link is done. Go to the Check.
   - If it isn't, go to step 8.

8. **Link Instagram (only if it's missing).** On the Page's panel, click the "Connect assets"
   button and choose Instagram. A window opens where they log in to Instagram with the
   professional account from chapter 2 and allow the link. Those clicks are theirs. When they say
   it's done, read the "Connected assets" tab again. Then write a note,
   `"03-facebook-page": "Instagram linked to the Page, still to check"`.

## Check

Any one of these shows their Instagram username from chapter 2 linked to their Page.

- On business.facebook.com/latest/settings, under Business assets, then Pages, then their Page,
  the "Connected assets" tab lists it. You can read this yourself, because nothing on that page is
  secret.
- In the Page's own settings on facebook.com, "Linked accounts" (or "Instagram") shows it. This is
  the place to look if they linked it there, or if a new Page doesn't show up in Business assets.
- In the Instagram app, on their profile, "Edit profile" has a "Page" line that names their
  Facebook Page. They read it out to you.

Tell them chapter 5's `igdm connect` confirms the link for certain, because it only works when
Meta sees the Page and the Instagram account linked.

When it passes, add `03-facebook-page` to `done` in `.setup-progress.json`, with the Page name in
the note (see [../reference/progress.md](../reference/progress.md)). Tell them their Instagram and
Facebook Page are linked, then go to chapter 4.

## If it doesn't work

- **Facebook refuses a change, or says something went wrong, with no clear reason.** Check the VPN
  is off. With a VPN on, Facebook has refused changes and only accepted them about 9 minutes after
  it was switched off. Wait ten minutes and try again.
- **Business Suite wants a business portfolio before it shows anything.** Use the Page itself
  instead. Ask them to open their Page on facebook.com, switch into it if Facebook asks, and open
  its Settings. Look for "Linked accounts" or "Instagram", and connect the Instagram account there.
  That same "Linked accounts" page then counts for the Check.
- **A new Page doesn't show up in Business assets.** That can happen when the Page was made on
  facebook.com and Business Suite hasn't picked it up. Link Instagram from the Page's own settings,
  as above, and use that page, or Instagram's "Edit profile", for the Check.
- **"Connect assets" doesn't offer Instagram, or the Instagram login fails.** The Meta Business
  Suite phone app can do the same link. Ask them to install it, log in with the same Facebook
  account, and connect their Instagram account there. Step by step, as for any phone step.
- **Their Page isn't in the list.** They're probably logged in to a Facebook account that isn't
  its admin. Ask them to check which account manages the Page, and log in with that one.
- **The Instagram account says it isn't eligible.** It's probably still a personal account. Go
  back to chapter 2.
- Meta changes these labels often. If a label differs, look for the nearest match and tell them
  what you're clicking instead. For anything else, follow
  [../reference/stuck.md](../reference/stuck.md) and
  [../reference/meta-notes.md](../reference/meta-notes.md).
