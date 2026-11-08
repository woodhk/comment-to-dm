"""Checking a reel form from the dashboard before it is written to config.toml."""

from datetime import datetime, timezone

import pytest
from fakes import repo_config

from igdm import voice
from igdm.instagram import InstagramError, Media
from igdm.reelform import GAP_RE, TEMPLATE_SUBJECT, check_reel, slug, template_body

UTC = timezone.utc
NOW = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)      # 10:00 SGT
POSTED = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)   # 09:00 SGT
TOKYO_URL = "https://www.instagram.com/reel/TESTREEL01/"


@pytest.fixture
def cfg():
    return repo_config()


def form(**kw):
    f = {"keyword": "Rome", "url": "https://www.instagram.com/reel/ROME1/",
         "offer": "the Rome slides", "material_url": "https://drive.google.com/x",
         "email_subject": "the {keyword} slides you asked for",
         "email_body": "Hey {name},\n\nHere's the material like I promised.\n\n{material_url}\n\nEnjoy it!\n\nAlex",
         "handle_comments_after": "", "hand_dm_phrases": [], "ai_notes": ""}
    f.update(kw)
    return f


class Finder:
    def __init__(self, media=Media("m2", POSTED), error=None):
        self.media, self.error, self.calls = media, error, []

    def __call__(self, shortcode):
        self.calls.append(shortcode)
        if self.error:
            raise self.error
        return self.media


def check(cfg, f, *, editing=None, has_leads=False, find=None):
    return check_reel(f, cfg, editing=editing, has_leads=has_leads,
                      find_media=find if find is not None else Finder(), now=NOW)


def test_template_has_gaps_and_passes_voice():
    assert len(GAP_RE.findall(TEMPLATE_SUBJECT)) == 1
    assert len(GAP_RE.findall(template_body("Sam"))) == 2
    assert voice.problems(TEMPLATE_SUBJECT, ()) == [] and voice.problems(template_body("Sam"), ()) == []


def test_slug():
    assert slug("AI Rome!") == "ai-rome"
    assert slug("  Tokyo ") == "tokyo"


def test_good_new_reel_has_no_errors(cfg):
    r = check(cfg, form())
    assert r.errors == {}
    assert r.key == "rome" and r.fields["key"] == "rome"
    assert r.posted_at == POSTED
    assert r.fields["handle_comments_after"] == "2026-11-04T09:00:00"


def test_required_fields(cfg):
    r = check(cfg, form(keyword="", url="", offer=" ", material_url="", email_subject="",
                        email_body=""))
    assert set(r.errors) >= {"keyword", "url", "offer", "material_url", "email_subject", "email_body"}


def test_bad_link_and_duplicate_link(cfg):
    assert "url" in check(cfg, form(url="https://example.com/x")).errors
    assert "url" in check(cfg, form(url=TOKYO_URL)).errors


def test_bad_material_link(cfg):
    assert "material_url" in check(cfg, form(material_url="drive folder")).errors


def test_duplicate_keyword_case_insensitive(cfg):
    assert "keyword" in check(cfg, form(keyword="toKYO")).errors


def test_unfilled_gap_is_an_error(cfg):
    r = check(cfg, form(email_body=template_body("Sam")))
    assert "[what's inside it]" in r.errors["email_body"]


def test_unknown_placeholder_is_an_error(cfg):
    r = check(cfg, form(email_body="Hey {first}, here's the material {material_url}"))
    assert "{name}" in r.errors["email_body"]
    assert "email_body" in check(cfg, form(email_body="Hey {name, broken")).errors


def test_voice_rules_apply(cfg):
    assert "email_subject" in check(cfg, form(email_subject="the slides — enjoy")).errors


def test_link_not_yours(cfg):
    r = check(cfg, form(), find=Finder(media=None))
    assert r.errors["url"] == "that link isn't one of your posts"


def test_instagram_error_on_link_check(cfg):
    r = check(cfg, form(), find=Finder(error=InstagramError(500, 1, "down")))
    assert "couldn't check the link with Instagram" in r.errors["url"]


def test_edit_with_leads_locks_keyword_and_link(cfg):
    sz = cfg.reels[0]
    f = form(keyword="Tokyo2", url="https://www.instagram.com/reel/OTHER/")
    r = check(cfg, f, editing="tokyo", has_leads=True)
    assert "keyword" in r.errors and "url" in r.errors
    assert r.key == "tokyo" and sz.key == "tokyo"


def test_edit_without_leads_can_change_link(cfg):
    r = check(cfg, form(keyword="Tokyo", url="https://www.instagram.com/reel/OTHER/"),
              editing="tokyo")
    assert r.errors == {}


def test_edit_unchanged_link_not_looked_up_again(cfg):
    find = Finder()
    r = check(cfg, form(keyword="Tokyo", url=TOKYO_URL, handle_comments_after="2026-11-03T14:00"),
              editing="tokyo", has_leads=True, find=find)
    assert r.errors == {} and find.calls == []


def test_crlf_normalised(cfg):
    r = check(cfg, form(email_body="Hey {name},\r\n\r\nHere's the material like I promised.\r\n{material_url}"))
    assert "\r" not in r.fields["email_body"]


def test_start_time_defaults_to_now_without_posted_at(cfg):
    r = check(cfg, form(), find=Finder(media=Media("m2", None)))
    assert r.fields["handle_comments_after"] == "2026-11-04T10:00:00"


def test_bad_start_time(cfg):
    assert "handle_comments_after" in check(cfg, form(handle_comments_after="tomorrow")).errors


def test_phrases_stripped_and_blank_dropped(cfg):
    r = check(cfg, form(hand_dm_phrases=["  from that Rome reel ", "", "  "]))
    assert r.fields["hand_dm_phrases"] == ["from that Rome reel"]


def test_first_dm_wording_is_kept_and_checked():
    from fakes import repo_config
    from igdm.reelform import check_reel
    cfg = repo_config()
    form = {"keyword": "Robots", "url": "https://www.instagram.com/reel/ABCdef123/", "offer": "robot slides",
            "material_url": "https://x.y/z", "email_subject": "s", "email_body": "Hey {name},\n\nhi",
            "dm_offer": " the robot slides "}
    r = check_reel(form, cfg, editing=None, has_leads=False, find_media=None)
    assert "dm_offer" not in r.errors and r.fields["dm_offer"] == "the robot slides"
    bad = check_reel({**form, "dm_offer": "the slides — all of them"}, cfg, editing=None,
                     has_leads=False, find_media=None)
    assert "dm_offer" in bad.errors


SZ_FOLDER = "https://example.com/material"


def test_same_material_names_other_reels_with_that_link(cfg):
    # only the ?usp=... part differs: still the same Drive folder
    assert check(cfg, form(material_url=SZ_FOLDER + "?usp=drive_link")).same_material == ["Tokyo"]
    assert check(cfg, form()).same_material == []


def test_same_material_leaves_out_the_reel_being_edited(cfg):
    f = form(keyword="Tokyo", url=TOKYO_URL, material_url=SZ_FOLDER)
    assert check(cfg, f, editing="tokyo").same_material == []
