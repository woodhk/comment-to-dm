import random
from pathlib import Path

from igdm.config import load_config
from igdm.messages import base_of, find_email, first_name, pick_got_it, pick_public_reply, render

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config(ROOT / "tests" / "data" / "config.toml")


def test_render_dm1():
    out = render(CFG.messages.dm1, name="sarah.travels_", keyword="Tokyo")
    assert out == (
        "hey sarah.travels_, it's Sam from that travel reel you commented Tokyo on. "
        "do you still want the guide?"
    )


def test_render_material_url():
    assert render("{material_url}!", name="a", keyword="k", material_url="http://x") == "http://x!"


def test_find_email_variants():
    assert find_email("sure it's Sarah@Gmail.com.") == "sarah@gmail.com"
    assert find_email("yes pls\nbob.smith+ig@uni.edu.hk") == "bob.smith+ig@uni.edu.hk"
    assert find_email("sarah at gmail dot com") is None
    assert find_email("yes") is None


def test_public_reply_avoids_recent():
    pool = ["check dms", "sent it over", "dmed you"]
    rng = random.Random(1)
    for _ in range(50):
        text = pick_public_reply(pool, ["check dms", "sent it over"], rng)
        assert base_of(text, pool) == "dmed you"


def test_public_reply_falls_back_when_all_recent():
    pool = ["a one", "b two"]
    text = pick_public_reply(pool, list(pool), random.Random(2))
    assert base_of(text, pool) in pool


def test_public_reply_variation_is_bounded():
    pool = CFG.messages.public_replies
    rng = random.Random(7)
    firsts = set()
    for _ in range(500):
        text = pick_public_reply(pool, [], rng)
        assert base_of(text, pool) in pool
        firsts.add(text[0])
    assert any(c.isupper() for c in firsts) and any(c.islower() for c in firsts)


def test_first_name():
    assert first_name("Sarah Thompson") == "Sarah"
    assert first_name("  ") is None
    assert first_name(None) is None


def test_base_of_longest_prefix():
    pool = ["check dms", "check dms please", "sent 📩"]
    assert base_of("Check dms please 👍", pool) == "check dms please"
    assert base_of("Check dms", pool) == "check dms"


def test_pick_got_it():
    assert pick_got_it(CFG.messages.got_it, random.Random(0)) in CFG.messages.got_it


def test_first_name_skips_tokens_without_letters():
    assert first_name("✨ Sam Lee") == "Sam"
    assert first_name("🇨🇳 🔥 jo") == "jo"
    assert first_name("✨ 🔥") is None  # caller falls back to the handle
    assert first_name("Zoë") == "Zoë"
