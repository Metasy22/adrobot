import pytest

from app.shares import even_split, rebalance, validate_shares


@pytest.mark.parametrize("n", range(1, 15))
def test_even_split_sums_to_100(n):
    parts = even_split(n)
    assert len(parts) == n
    assert sum(parts) == 100
    assert max(parts) - min(parts) <= 1


def test_examples():
    assert even_split(0) == []
    assert even_split(2) == [50, 50]
    assert even_split(3) == [34, 33, 33]
    assert even_split(5) == [20] * 5
    assert rebalance([7, 8, 9]) == {7: 34, 8: 33, 9: 33}


def test_validate():
    validate_shares({1: 60, 2: 40})
    with pytest.raises(ValueError):
        validate_shares({1: 60, 2: 30})
    with pytest.raises(ValueError):
        validate_shares({1: 120, 2: -20})


# --- pinned / hidden / removed ---
from types import SimpleNamespace  # noqa: E402

from app.shares import recalc, visible_total  # noqa: E402


def row(oid, share=0, pinned=False, hidden=False, removed=False):
    return SimpleNamespace(offer_id=oid, share=share, pinned=pinned, hidden=hidden, removed=removed)


def shares(rows):
    return [r.share for r in rows]


def test_recalc_even():
    rows = [row(1), row(2), row(3)]
    recalc(rows)
    assert shares(rows) == [34, 33, 33]


def test_recalc_with_pin():
    rows = [row(1, 50, pinned=True), row(2), row(3), row(4)]
    recalc(rows)
    assert shares(rows) == [50, 17, 17, 16]
    assert visible_total(rows) == 100


def test_recalc_hidden_and_removed_get_zero():
    rows = [row(1, 30, pinned=True), row(2, 40, hidden=True), row(3, 40, removed=True), row(4), row(5)]
    recalc(rows)
    assert shares(rows) == [30, 0, 0, 35, 35]
    assert visible_total(rows) == 100


def test_hidden_pinned_releases_its_share():
    rows = [row(1, 60, pinned=True, hidden=True), row(2), row(3)]
    recalc(rows)
    assert shares(rows) == [0, 50, 50]


def test_all_pinned_keeps_values():
    rows = [row(1, 70, pinned=True), row(2, 20, pinned=True)]
    recalc(rows)
    assert shares(rows) == [70, 20]
    assert visible_total(rows) == 90  # caller must refuse to commit


def test_pins_over_100_leave_zero_for_free():
    rows = [row(1, 100, pinned=True), row(2)]
    recalc(rows)
    assert shares(rows) == [100, 0]
