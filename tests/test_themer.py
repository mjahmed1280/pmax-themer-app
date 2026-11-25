from app.ads.base import AdsError, ChunkResult
from app.ads.mock import MockBackend
from app.ads.themer import build_plan, process_chunks

TARGET = {"customer_id": "2000000001", "login_customer_id": "1000000001", "campaign_id": "30000001"}


def test_dedupe_is_case_and_space_insensitive_and_keeps_order():
    plan = build_plan(["Alpha  fund", "beta", "alpha fund", " BETA ", "gamma"])
    assert [t for c in plan.chunks for t in c.themes] == ["Alpha fund", "beta", "gamma"]
    assert plan.stats["duplicates_removed"] == 2


def test_chunking_25_25_6():
    plan = build_plan([f"kw {i}" for i in range(56)])
    assert plan.stats["sizes"] == [25, 25, 6]
    assert [c.name for c in plan.chunks] == ["PMax Themer 1/3", "PMax Themer 2/3", "PMax Themer 3/3"]


def test_bucket_size_is_capped_at_25():
    assert build_plan([f"k{i}" for i in range(60)], bucket_size=500).stats["sizes"] == [25, 25, 10]


def test_overlong_theme_is_skipped_not_sent():
    plan = build_plan(["ok", "x" * 81])
    assert plan.stats["unique"] == 1 and plan.stats["too_long"] == 1


def test_empty_input():
    assert build_plan(["", "  "]).chunks == []


def test_banned_themes_removed_and_retried():
    plan = build_plan(["good one", "guaranteed returns fund", "good two"])
    res = process_chunks(MockBackend(), TARGET, {}, plan, True, validate_only=False)
    assert res[0]["status"] == "created"
    assert res[0]["themes"] == ["good one", "good two"]
    assert [b["theme"] for b in res[0]["banned"]] == ["guaranteed returns fund"]


def test_validate_only_writes_nothing():
    be = MockBackend()
    res = process_chunks(be, TARGET, {}, build_plan(["a b", "c d"]), True, validate_only=True)
    assert res[0]["status"] == "validated" and be.created == []


def test_all_banned_is_skipped():
    res = process_chunks(MockBackend(), TARGET, {}, build_plan(["guaranteed returns x"]), True, False)
    assert res[0]["status"] == "skipped"


class _AlwaysRejects:
    calls = 0

    def create_asset_group(self, **kw):
        self.calls += 1
        return ChunkResult(ok=False, banned=[{"theme": kw["themes"][0], "policy": "p"}])


def test_retries_are_bounded():
    be = _AlwaysRejects()
    res = process_chunks(be, TARGET, {}, build_plan([f"t{i}" for i in range(20)]), True, False)
    assert res[0]["status"] == "failed" and be.calls == 5     # notebook would loop forever


class _Boom:
    def create_asset_group(self, **kw):
        raise AdsError("nope")


def test_unknown_error_fails_chunk_without_loop_and_continues():
    res = process_chunks(_Boom(), TARGET, {}, build_plan([f"t{i}" for i in range(30)]), True, False)
    assert [r["status"] for r in res] == ["failed", "failed"]
