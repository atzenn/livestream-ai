"""Live chat interaction for livestream-ai Phase 2 (YouTube-first).

Flow:
  poll liveChatMessages.list -> parse -> classify -> draft reply
  -> approval queue -> operator approves -> post to chat + avatar speaks

The study (rev2 sections 7.4 and 11 Pha 4) requires human-in-the-loop
for price / promo / policy answers: the avatar must not promise a
price, a discount, or a delivery date on its own. So every draft about
a product, a price, or buying waits in the queue until the operator
approves it. Greetings may auto-reply, but only when the operator
turns that on in config (default off: everything waits).

Replies are templates filled from the catalog only. There is no LLM
call in this module, so a reply cannot invent a price, a discount, or
a product that is not in the catalog. An LLM draft step is a later
phase; when it lands it must keep the same rule (draft from catalog
data, approval before speaking).

Quota (YouTube Data API v3, study rev2 section 5.3): liveChatMessages
list costs 5 units per call, insert/delete 20. Default poll is 10 s:
about 1,800 units/hour for polling, inside the 10,000/day quota for a
single live session. Poll faster only if the quota allows it.

Shopee / TikTok live comments are NOT read here: the study found no
confirmed public API for them (Shopee: one endpoint seen only in a
job posting, needs Partner rights; TikTok Shop Partner API covers
product/order/performance, not live chat control). The catalog links
still work on those platforms (post the link, show the card), but live
comment reading there stays a Phase 3 item behind partner approval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .catalog import Product, find_product, format_price_vnd
from .compliance import check_text

# Kinds a comment can be. Only product / price / buy need approval;
# greeting is auto-replyable when config allows it.
KIND_PRODUCT = "product_question"
KIND_PRICE = "price_question"
KIND_BUY = "buy_intent"
KIND_GREETING = "greeting"
KIND_SPAM = "spam"
KIND_OTHER = "other"

_PRICE_WORDS = (
    "gia",
    "bao nhieu",
    "gia bao",
    "sale",
    "khuyen mai",
    "giam gia",
    "re khong",
    "dat khong",
)
_BUY_WORDS = (
    "mua",
    "dat hang",
    "chot",
    "lay 1",
    "mua o dau",
    "link mua",
    "gio hang",
    "order",
)
_GREETING_WORDS = ("hello", "hi ", "chao", "xin chao", "alo")
_SPAM_WORDS = ("kiem tien", "sub4sub", "follow cheo", "tang follow", "dang ky kenh")


@dataclass
class ChatMessage:
    id: str
    author: str
    text: str
    published_at: str = ""


@dataclass
class DraftReply:
    message_id: str
    author: str
    kind: str
    text: str
    product_id: str = ""
    needs_approval: bool = True
    blocked: bool = False
    block_reason: str = ""


def parse_chat_response(resp: dict) -> tuple[list[ChatMessage], str]:
    """Parse a liveChatMessages.list response.

    Returns (messages, next_page_token). Malformed items are skipped,
    never crash the poll loop.
    """
    messages: list[ChatMessage] = []
    if not isinstance(resp, dict):
        return messages, ""
    for item in resp.get("items") or []:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet") or {}
        text = str(snippet.get("displayMessage", "")).strip()
        if not text:
            continue
        author = ""
        author_details = item.get("authorDetails") or {}
        if isinstance(author_details, dict):
            author = str(author_details.get("displayName", ""))
        messages.append(
            ChatMessage(
                id=str(item.get("id", "")),
                author=author,
                text=text,
                published_at=str(snippet.get("publishedAt", "")),
            )
        )
    token = resp.get("nextPageToken") or ""
    return messages, str(token)


def classify(text: str, products: list[Product]) -> tuple[str, Product | None]:
    """Classify one comment. Returns (kind, matched product or None)."""
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered:
        return KIND_SPAM, None
    for word in _SPAM_WORDS:
        if word in lowered:
            return KIND_SPAM, None
    product = find_product(text, products)
    # Buy words first: "gia do" (stand) contains "gia", so a bare
    # price-word check would misread a buy question about a stand as
    # a price question. Buy replies include the price + link anyway.
    if any(w in lowered for w in _BUY_WORDS):
        return KIND_BUY, product
    if any(w in lowered for w in _PRICE_WORDS):
        return KIND_PRICE, product
    if product is not None:
        return KIND_PRODUCT, product
    for word in _GREETING_WORDS:
        if word in lowered:
            return KIND_GREETING, None
    return KIND_OTHER, None


def draft_reply(
    message: ChatMessage,
    products: list[Product],
    max_chars: int = 200,
) -> DraftReply | None:
    """Draft a reply for one comment. None means: no reply, skip it.

    Spam and off-topic comments get no reply. Product / price / buy
    replies use catalog data only and need operator approval.
    Greetings get a short reply that needs no approval (the caller
    still decides whether auto-reply is on).
    """
    kind, product = classify(message.text, products)
    if kind in (KIND_SPAM, KIND_OTHER):
        return None
    if kind == KIND_GREETING:
        text = f"Chao {message.author or 'ban'}, cam on ban da vao xem live nhe."
        return DraftReply(
            message_id=message.id,
            author=message.author,
            kind=kind,
            text=text,
            needs_approval=False,
        )
    if product is None:
        # Price/buy question with no product matched: do not guess.
        text = (
            "Ban cho minh xin ten hoac ma san pham nhe, "
            "minh bao gia va gui link gio hang cho ban."
        )
        return DraftReply(
            message_id=message.id,
            author=message.author,
            kind=kind,
            text=text,
            needs_approval=True,
        )
    if not product.in_stock:
        text = (
            f"{product.name} hien het hang roi ban oi, "
            "ban theo doi live sau nhe."
        )
    elif kind == KIND_PRICE:
        text = (
            f"{product.name} gia {format_price_vnd(product.price_vnd)} ban nhe. "
            f"Link gio hang: {product.link}"
        )
    elif kind == KIND_BUY:
        text = (
            f"Ban bam link nay de dat {product.name} nhe: {product.link} "
            f"Gia {format_price_vnd(product.price_vnd)}."
        )
    else:
        note = f" {product.note}" if product.note else ""
        text = (
            f"{product.name} gia {format_price_vnd(product.price_vnd)}.{note} "
            f"Link gio hang: {product.link}"
        )
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    draft = DraftReply(
        message_id=message.id,
        author=message.author,
        kind=kind,
        text=text,
        product_id=product.id,
        needs_approval=True,
    )
    # The compliance gate applies to avatar replies too: a blocked
    # draft never speaks and never posts until the operator rewrites it.
    result = check_text(text)
    if not result.passed:
        draft.blocked = True
        draft.block_reason = ", ".join(
            f"{v.rule} ({v.matched})" for v in result.violations
        )
    return draft


class ApprovalQueue:
    """Append-only JSONL approval queue for chat replies.

    One line per draft: status pending / approved / rejected. The
    operator approves by id (see scripts/run_live_chat.py). The queue
    file is the audit trail: who asked, what was drafted, what was
    approved, and when.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, draft: DraftReply, original_text: str) -> dict:
        entry = {
            "id": f"{draft.message_id}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "author": draft.author,
            "kind": draft.kind,
            "original": original_text,
            "draft": draft.text,
            "product_id": draft.product_id,
            "needs_approval": draft.needs_approval,
            "blocked": draft.blocked,
            "block_reason": draft.block_reason,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def read_all(self) -> list[dict]:
        if not self.path.is_file():
            return []
        out: list[dict] = []
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def pending(self) -> list[dict]:
        return [e for e in self.read_all() if e.get("status") == "pending"]

    def set_status(self, entry_id: str, status: str) -> dict | None:
        """Approve or reject one entry. Returns it, or None if not found."""
        if status not in ("approved", "rejected"):
            raise ValueError(f"invalid queue status: {status}")
        entries = self.read_all()
        found: dict | None = None
        for e in entries:
            if str(e.get("id")) == str(entry_id):
                e["status"] = status
                e["decided_ts"] = datetime.now(timezone.utc).isoformat()
                found = e
        if found is None:
            return None
        with self.path.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return found


@dataclass
class ChatPollState:
    """Dedup + paging state for one live session. Kept in memory only."""

    seen_ids: set[str] = field(default_factory=set)
    page_token: str = ""

    def fresh(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        """Drop messages already seen. Updates the seen set."""
        out: list[ChatMessage] = []
        for m in messages:
            if m.id and m.id not in self.seen_ids:
                self.seen_ids.add(m.id)
                out.append(m)
            elif not m.id:
                out.append(m)
        return out
