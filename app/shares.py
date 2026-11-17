"""Traffic share math for offers inside a stream.

Keitaro stores `share` as an integer percent per offer. Rules used by the editor:

* removed and hidden offers get 0;
* pinned offers keep their share;
* the rest (100 - pinned total) is split evenly between unpinned visible offers,
  earlier offers absorbing the remainder (e.g. 3 offers -> 34/33/33).
"""

from collections.abc import Iterable
from typing import Protocol


class OfferRow(Protocol):
    offer_id: int
    share: int
    pinned: bool
    hidden: bool
    removed: bool


def even_split(count: int, total: int = 100) -> list[int]:
    if count <= 0:
        return []
    base, rest = divmod(total, count)
    return [base + 1 if i < rest else base for i in range(count)]


def rebalance(offer_ids: list[int]) -> dict[int, int]:
    """Even split across offers, preserving order; earlier offers absorb the remainder."""
    return dict(zip(offer_ids, even_split(len(offer_ids))))


def is_visible(row: OfferRow) -> bool:
    return not row.removed and not row.hidden


def pinned_total(rows: Iterable[OfferRow], exclude_offer_id: int | None = None) -> int:
    return sum(r.share for r in rows if is_visible(r) and r.pinned and r.offer_id != exclude_offer_id)


def recalc(rows: list[OfferRow]) -> None:
    """Recompute shares in place according to the rules above."""
    free_rows = []
    for r in rows:
        if not is_visible(r):
            r.share = 0
        elif not r.pinned:
            free_rows.append(r)
    free = max(0, 100 - pinned_total(rows))
    for r, share in zip(free_rows, even_split(len(free_rows), free)):
        r.share = share


def visible_total(rows: Iterable[OfferRow]) -> int:
    return sum(r.share for r in rows if is_visible(r))


def validate_shares(shares: dict[int, int]) -> None:
    if not shares:
        return
    if any(s < 0 or s > 100 for s in shares.values()):
        raise ValueError("Each share must be between 0 and 100")
    if sum(shares.values()) != 100:
        raise ValueError(f"Shares must sum to 100, got {sum(shares.values())}")
