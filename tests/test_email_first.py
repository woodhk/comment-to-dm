"""Email-first flow: the first DM asks for the email, and a DM confirms once the email is sent."""

from dataclasses import replace
from datetime import timedelta

import pytest
from fakes import FakeMailer
from test_engine_replies import MEDIA
from test_replies import REnv, route

from igdm.db import Status
from igdm.engine import _send_emails, ingest_comments, process_replies, send_due_dm
from igdm.instagram import Comment

DM1 = "hey 👋 just drop your email and i'll send you {offer}"
GOT = ["thank you! let me get this sent over now"]
SENT = ["just sent it to {email}, check your spam if you don't see it :)"]


class EFEnv(REnv):
    def __init__(self, dm_offer="the Tokyo slides"):
        super().__init__()
        m = replace(self.cfg.messages, ask_email_first=True, dm1=DM1, got_it=GOT, sent_dm=SENT)
        reels = [replace(self.cfg.reels[0], dm_offer=dm_offer)]
        self.cfg = replace(self.cfg, messages=m, reels=reels)
        self.ctx.config = self.cfg
        self.m = self.cfg.messages
        self.mail = FakeMailer()
        self.ctx.mailer = self.mail
        self.first_dms = []

    def first_dm(self, cid="c1", username="sarah", text="Tokyo", media=MEDIA):
        self.ig.comments.setdefault(media, []).append(Comment(cid, text, username, None, self.t))
        ingest_comments(self.ctx)
        for _ in range(5):
            self.advance(minutes=1)
            if send_due_dm(self.ctx):
                break
        self.first_dms += [s for s in self.ig.sent if s[0] == "private"]
        r = self.db.get(cid)
        self.ig.sent.clear()
        self.ig.actions.clear()
        self.sleeps.clear()
        self.jev.calls.clear()
        return r

    def captured(self, email="sarah@gmail.com"):
        """First DM, then they send their email and get the got-it DM."""
        r = self.first_dm()
        self.say(r.igsid, email)
        self.advance(seconds=10)
        assert process_replies(self.ctx) == 1
        self.ig.sent.clear()
        return r.igsid

    def email_goes_out(self):
        self.advance(minutes=3)
        return _send_emails(self.ctx)


@pytest.fixture
def env():
    return EFEnv()


def test_first_dm_asks_for_the_email_straight_away(env):
    r = env.first_dm()
    (dm,) = env.first_dms
    assert dm[2] == "hey 👋 just drop your email and i'll send you the Tokyo slides"
    assert r.status is Status.AWAITING_EMAIL and r.asks == 1
    assert r.dm1_text == dm[2]


def test_a_reel_without_its_own_wording_sends_the_material():
    env = EFEnv(dm_offer="")
    env.first_dm()
    assert env.first_dms[0][2].endswith("i'll send you the material")


def test_first_dm_doesnt_look_up_their_name_when_it_isnt_used(env):
    looked = []
    env.ig.get_display_name = lambda uid: looked.append(uid)
    env.first_dm()
    assert looked == []


def test_email_gets_got_it_then_a_sent_dm_once_gmail_takes_it(env):
    igsid = env.captured()
    assert env.status() is Status.EMAIL_CAPTURED
    assert env.mail.sent == []  # the email itself goes a couple of minutes later
    assert env.email_goes_out() == 1
    assert [to for to, _, _ in env.mail.sent] == ["sarah@gmail.com"]
    assert env.dms() == [("dm", igsid, "just sent it to sarah@gmail.com, check your spam if you don't see it :)")]
    (e,) = env.db.emails()
    assert e.confirm_state == "sent"


def test_the_sent_dm_goes_out_only_once(env):
    env.captured()
    env.email_goes_out()
    env.ig.sent.clear()
    env.advance(minutes=1)
    _send_emails(env.ctx)
    assert env.dms() == []


def test_no_sent_dm_if_alex_typed_in_the_chat_after_their_email(env):
    igsid = env.captured()
    env.ig.add_own_message(igsid, "hey it's alex, sending it now", env.t + timedelta(seconds=30))
    env.email_goes_out()
    assert len(env.mail.sent) == 1 and env.dms() == []
    assert env.db.emails()[0].confirm_state == "skipped"


def test_no_sent_dm_for_emails_sent_with_send_material(env):
    reel = env.cfg.reels[0]
    env.db.add_manual_email(name="M", handle="marwanezb", email="m@x.com", reel_url=reel.url,
                            keyword=reel.keyword, now=env.t)
    env.email_goes_out()
    assert len(env.mail.sent) == 1 and env.dms() == []


def test_no_sent_dm_when_gmail_refuses_the_address(env):
    from igdm.mailer import MailRefused
    env.mail.fail["sarah@gmail.com"] = MailRefused("550 no such user")
    env.captured()
    env.email_goes_out()
    env.advance(minutes=3)
    env.email_goes_out()  # the retry fails too
    assert env.dms() == []


def test_a_crash_while_sending_the_sent_dm_is_never_retried(env):
    env.captured()

    def boom(*a):
        raise RuntimeError("laptop lid closed")
    env.ig.send_message = boom
    with pytest.raises(RuntimeError):
        env.email_goes_out()
    env.ig.send_message = type(env.ig).send_message.__get__(env.ig)
    env.advance(minutes=1)
    _send_emails(env.ctx)
    assert env.dms() == []
    assert env.db.emails()[0].confirm_state == "unknown"


def test_a_late_sent_dm_is_skipped(env):
    env.captured()
    env.ctx.mailer = None  # the bot can't email for a while
    env.email_goes_out()
    env.ctx.mailer = env.mail
    env.email_goes_out()  # the email goes out now...
    (e,) = env.db.emails()
    env.db.mark_email(e.id, "sent", at=env.t - timedelta(hours=2))  # ...but say it went 2 h ago
    env.db.set_confirm(e.id, "pending")
    env.ig.sent.clear()
    _send_emails(env.ctx)
    assert env.dms() == [] and env.db.emails()[0].confirm_state == "skipped"


def test_no_email_in_the_reply_uses_the_asked_bank(env):
    r = env.first_dm()
    env.reply(r.igsid, "yes pls", route(asked_yes=0.9))
    (dm,) = env.dms()
    assert dm[2] in env.entry("asked_yes").replies
    assert env.db.get("c1").asks == 2 and env.status() is Status.AWAITING_EMAIL


def test_an_unanswered_email_ask_doesnt_block_a_later_reel_for_good(env):
    second = replace(env.cfg.reels[0], key="ai", keyword="AI", dm_offer="the AI slides",
                     url="https://www.instagram.com/reel/TESTREEL02/",
                     material_url="https://drive.google.com/drive/folders/AI-SLIDES")  # its own material
    env.cfg = replace(env.cfg, reels=[env.cfg.reels[0], second])
    env.ctx.config = env.cfg
    env.ctx.media_ids["ai"] = "m2"
    env.jev.comment_probs["AI"] = 0.95
    env.first_dm("c1")
    env.advance(hours=2)
    r2 = env.first_dm("c2", text="AI", media="m2")  # within 24 h: waits
    assert r2.status is Status.WAITING
    env.advance(hours=23)
    assert send_due_dm(env.ctx)
    assert env.status("c2") is Status.AWAITING_EMAIL and env.status("c1") is Status.SUPERSEDED


def test_no_reply_yet_lists_unanswered_email_asks(env):
    from igdm.stats import dashboard_state
    env.first_dm()
    nr = dashboard_state(env.db, env.cfg, env.t)["no_reply"]
    assert [r["handle"] for r in nr["bot"]] == ["sarah"]


# ---- review fixes

def test_emails_still_refuse_offer_in_curly_brackets():
    from fakes import repo_config
    from igdm.reelform import check_reel
    form = {"keyword": "Robots", "url": "https://www.instagram.com/reel/ABCdef123/", "offer": "slides",
            "material_url": "https://x.y/z", "email_subject": "s",
            "email_body": "Hey {name},\n\nhere is {offer} like i promised"}
    r = check_reel(form, repo_config(), editing=None, has_leads=False, find_media=None)
    assert "email_body" in r.errors


@pytest.mark.parametrize("old, new", [
    ('dm1 = "', 'dm1 = "{offers} '),
    ('repeat_dm1 = "', 'repeat_dm1 = "{nope} '),
])
def test_first_dm_typos_are_refused_when_the_config_loads(tmp_path, old, new):
    from fakes import ROOT
    from igdm.config import load_config
    text = (ROOT / "tests" / "data" / "config.toml").read_text()
    assert old in text
    p = tmp_path / "c.toml"
    p.write_text(text.replace(old, new, 1))
    with pytest.raises(ValueError, match="dm1"):
        load_config(p)


@pytest.mark.parametrize("line", ['sent_dm = "just sent it to you"', 'sent_dm = [3]',
                                  'sent_dm = ["sent to {email.x}"]'])
def test_sent_dm_must_be_a_list_of_wordings(tmp_path, line):
    from fakes import ROOT
    from igdm.config import load_config
    text = (ROOT / "tests" / "data" / "config.toml").read_text().replace("[messages]\n", f"[messages]\n{line}\n", 1)
    p = tmp_path / "c.toml"
    p.write_text(text)
    with pytest.raises(ValueError, match="sent_dm"):
        load_config(p)


def test_a_conversation_handed_back_by_owner_isnt_treated_as_unanswered(env):
    from igdm.engine import _blocks
    r = env.first_dm()
    env.db.update("c1", status=Status.NEEDS_OWNER, paused_from=Status.AWAITING_EMAIL)
    assert env.db.carry_on("c1", env.t)  # Alex: let the bot carry on
    back = env.db.get("c1")
    assert back.status is Status.AWAITING_EMAIL and back.last_user_msg_at is None
    assert _blocks(back, env.t + timedelta(days=3))  # never dropped for another reel's DM


def test_a_replied_email_ask_blocks_a_new_first_dm_for_good(env):
    from igdm.engine import _blocks
    r = env.first_dm()
    env.reply(r.igsid, "yes pls", route(asked_yes=0.9))
    assert _blocks(env.db.get("c1"), env.t + timedelta(days=3))


def test_old_do_you_want_it_conversations_carry_on_after_the_switch():
    from test_replies import REnv
    old = REnv()
    igsid = old.live()  # DM 1 under the old flow
    old.cfg = replace(old.cfg, messages=replace(old.cfg.messages, ask_email_first=True, dm1=DM1))
    old.ctx.config = old.cfg
    assert old.reply(igsid, "yes pls", route(yes=0.95)) == 1
    assert [d[2] for d in old.dms()] == [old.m.followup_1, old.m.followup_2]


def _sent_dm_attempt(env, err):
    igsid = env.captured()
    sent = type(env.ig).send_message.__get__(env.ig)

    def send(own, to, text):
        if text.startswith("just sent it"):
            raise err
        return sent(own, to, text)
    env.ig.send_message = send
    env.email_goes_out()
    return env.db.emails()[0].confirm_state


def test_sent_dm_rate_limited_waits_and_backs_off(env):
    from igdm.engine import REPLY_BACKOFF_KEY
    from igdm.instagram import RateLimited
    assert _sent_dm_attempt(env, RateLimited(429, 4, "slow down")) == "pending"
    assert env.db.kv_get(REPLY_BACKOFF_KEY)


def test_sent_dm_with_no_answer_is_never_retried(env):
    from igdm.instagram import MaybeSent
    assert _sent_dm_attempt(env, MaybeSent(0, None, "timed out")) == "unknown"


def test_sent_dm_refused_is_given_up(env):
    from igdm.instagram import InstagramError
    assert _sent_dm_attempt(env, InstagramError(400, 10, "outside the window")) == "failed"


def test_sent_dm_skipped_once_the_24h_window_closed(env):
    env.captured()
    (e,) = env.db.emails()
    env.db.update("c1", last_user_msg_at=env.t - timedelta(hours=25))
    env.email_goes_out()
    assert env.dms() == [] and env.db.emails()[0].confirm_state == "skipped"


def test_sent_dm_waits_while_the_reel_is_paused(env):
    env.captured()
    env.advance(minutes=3)
    from igdm.outbox import send_due_emails
    send_due_emails(env.ctx)  # the email goes (Send now style rows aside, it was queued before)
    env.ctx.config = replace(env.cfg, reels=[replace(env.cfg.reels[0], paused=True)])
    _send_emails(env.ctx)
    assert env.dms() == [] and env.db.emails()[0].confirm_state == "pending"
    env.ctx.config = env.cfg
    _send_emails(env.ctx)
    assert len(env.dms()) == 1 and env.db.emails()[0].confirm_state == "sent"
