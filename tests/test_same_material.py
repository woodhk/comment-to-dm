"""Reels that give away the same material count as one offer.

Bug (6 Nov 2026): jo.walks got the slides from the AI reel, then commented on the Tokyo
reel (same Drive folder) and was DM'd "can i send the Tokyo stuff to the same email as
before?". The duplicate check only looked at the same reel.
"""

from dataclasses import replace
from datetime import timedelta

from test_engine_outreach import T9, Env

from igdm.db import Request, Status
from igdm.engine import ingest_comments

FOLDER = "https://example.com/material"


def two_reels(ai_material: str) -> Env:
    """The fixture's Tokyo reel plus an AI reel, as in the live config."""
    env = Env()
    sz = replace(env.cfg.reels[0], material_url=FOLDER + "?usp=sharing")
    ai = replace(sz, key="ai", keyword="AI", url="https://www.instagram.com/reel/TESTREEL02/",
                 shortcode="TESTREEL02", material_url=ai_material)
    env.cfg = replace(env.cfg, reels=[sz, ai])
    env.ctx.config = env.cfg
    return env


def earlier_ai_request(env: Env, status: Status) -> None:
    env.db.insert_request(Request(
        comment_id="ai1", reel_key="ai", media_id="m2", username="jo.walks", commenter_id="u9",
        text="Ai", commented_at=T9 - timedelta(hours=12), status=status, igsid="igsid-9"))


def test_same_folder_on_another_reel_is_a_duplicate():
    env = two_reels(FOLDER + "?usp=drive_link")
    earlier_ai_request(env, Status.EMAIL_CAPTURED)
    env.comment("sz1", username="jo.walks")
    env.clock.advance(seconds=5)
    ingest_comments(env.ctx)
    assert env.db.get("sz1").status is Status.DUPLICATE
    assert env.db.get("sz1").dm_due_at is None  # nothing will be sent


def test_same_folder_still_mid_chat_is_a_duplicate():
    env = two_reels(FOLDER + "?usp=drive_link")
    earlier_ai_request(env, Status.AWAITING_EMAIL)
    env.comment("sz1", username="jo.walks")
    env.clock.advance(seconds=5)
    ingest_comments(env.ctx)
    assert env.db.get("sz1").status is Status.DUPLICATE


def test_different_material_on_another_reel_is_still_a_new_request():
    env = two_reels("https://drive.google.com/drive/folders/SOMETHING-ELSE")
    earlier_ai_request(env, Status.EMAIL_CAPTURED)
    env.comment("sz1", username="jo.walks")
    env.clock.advance(seconds=5)
    ingest_comments(env.ctx)
    assert env.db.get("sz1").status is Status.QUEUED


def test_an_off_topic_comment_on_the_other_reel_does_not_block():
    env = two_reels(FOLDER + "?usp=drive_link")
    earlier_ai_request(env, Status.LEFT_FOR_OWNER)
    env.comment("sz1", username="jo.walks")
    env.clock.advance(seconds=5)
    ingest_comments(env.ctx)
    assert env.db.get("sz1").status is Status.QUEUED


def test_same_material_ignores_link_options_but_not_the_folder():
    from igdm.engine import same_material
    env = two_reels(FOLDER + "?usp=drive_link")
    sz, ai = env.cfg.reels
    assert same_material(sz, ai)
    assert same_material(sz, replace(ai, material_url=FOLDER + "/"))
    assert not same_material(sz, replace(ai, material_url=FOLDER + "X"))
    assert not same_material(sz, replace(ai, material_url=""))
    assert same_material(sz, sz)
