#!/usr/bin/env python3
"""Phase 2 live chat runner: poll YouTube live chat, draft replies,
queue them for operator approval, post approved replies.

Usage (dry run, no network, no OAuth):
  python scripts/run_live_chat.py --config config.yaml \\
      --dry-run --fixture tests/fixtures/chat-sample.json

Usage (live, needs operator OAuth + a live broadcast):
  python scripts/run_live_chat.py --config config.yaml
  # polls live_chat.live_chat_id every poll_interval_s, prints new
  # comments + drafts, appends drafts to logs/chat-queue.jsonl

Approve one queued reply (posts it to chat):
  python scripts/run_live_chat.py --config config.yaml \\
      --approve <message-id>
  # with --speak also synthesizes the reply with TTS (mock by default
  # in dry runs; real engine comes from config) so the avatar can say
  # it. Without --speak, the approved text is only posted to chat.

List the pending queue:
  python scripts/run_live_chat.py --config config.yaml --list-pending

Reject one queued reply:
  python scripts/run_live_chat.py --config config.yaml \\
      --reject <message-id>

Exit codes: 0 ok, 1 config / API / queue error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.catalog import (
    load_products,
    product_card_text,
    write_product_card_html,
)
from livestream_ai.config import load_config
from livestream_ai.live_chat import (
    ApprovalQueue,
    ChatPollState,
    draft_reply,
    parse_chat_response,
)
from livestream_ai.youtube_live import YouTubeLiveClient


def _handle_messages(messages, products, queue, cfg, obs=None, out_dir="out/chat", post=None):
    """Classify + draft + queue fresh messages. Returns (queued, skipped).

    post: optional callable(text) -> message id. When a greeting draft
    needs no approval and auto_reply_greeting is on, it is posted at
    once through post (live mode only). All other drafts wait in the
    queue for --approve.
    """
    queued = 0
    skipped = 0
    for msg in messages:
        draft = draft_reply(msg, products, cfg.live_chat.max_reply_chars)
        if draft is None:
            print(f"skip [{msg.author}]: {msg.text}")
            skipped += 1
            continue
        if draft.blocked:
            print(f"BLOCKED [{msg.author}]: {draft.block_reason} -- {msg.text}")
            queue.add(draft, msg.text)
            queued += 1
            continue
        auto = (
            not draft.needs_approval
            and cfg.live_chat.auto_reply_greeting
        )
        entry = queue.add(draft, msg.text)
        print(f"queue [{msg.author}] ({draft.kind}): {draft.text}")
        if auto and post is not None:
            try:
                msg_id = post(draft.text)
                queue.set_status(entry["id"], "approved")
                print(f"  auto-posted (greeting): {msg_id}")
            except Exception as exc:
                print(f"  auto-post failed (stays pending): {exc}")
        elif draft.product_id:
            product = next(
                (p for p in products if p.id == draft.product_id), None
            )
            if product is not None and obs is not None:
                try:
                    obs.set_overlay_text(
                        cfg.cart.product_overlay_source,
                        product_card_text(product),
                    )
                    write_product_card_html(
                        product, str(Path(out_dir) / "product-card.html")
                    )
                except Exception as exc:
                    print(f"  overlay update failed (ignored): {exc}")
        queued += 1
    return queued, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="livestream-ai phase 2 live chat")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--out", default="out/chat")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fixture", default="tests/fixtures/chat-sample.json")
    ap.add_argument("--list-pending", action="store_true")
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--speak", action="store_true")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
    except Exception as exc:
        print(f"config error: {exc}")
        return 1
    if not cfg.cart.products_path:
        print("config error: cart.products_path is empty (see products.example.yaml)")
        return 1
    try:
        products = load_products(cfg.cart.products_path)
    except Exception as exc:
        print(f"catalog error: {exc}")
        return 1
    queue = ApprovalQueue(cfg.live_chat.queue_path)

    if args.list_pending:
        pending = queue.pending()
        print(f"pending: {len(pending)}")
        for e in pending:
            print(f"- {e['id']} [{e['author']}] ({e['kind']}): {e['draft']}")
        return 0

    if args.reject:
        entry = queue.set_status(args.reject, "rejected")
        if entry is None:
            print(f"queue entry not found: {args.reject}")
            return 1
        print(f"rejected: {entry['id']} [{entry['author']}]")
        return 0

    if args.approve:
        entries = queue.read_all()
        entry = next(
            (e for e in entries if str(e.get("id")) == args.approve), None
        )
        if entry is None:
            print(f"queue entry not found: {args.approve}")
            return 1
        if entry.get("status") != "pending":
            print(f"queue entry {args.approve} is already {entry.get('status')}")
            return 1
        if entry.get("blocked"):
            print(f"REFUSED: entry {args.approve} is blocked by compliance: {entry.get('block_reason')}")
            return 1
        text = entry["draft"]
        if args.dry_run:
            print(f"approved (dry run, not posted): {text}")
        else:
            if not cfg.youtube.client_secrets_path:
                print("youtube.client_secrets_path is empty in config.")
                return 1
            client = YouTubeLiveClient(
                cfg.youtube.client_secrets_path, cfg.youtube.token_path
            )
            try:
                msg_id = client.insert_chat_message(
                    cfg.live_chat.live_chat_id, text
                )
            except Exception as exc:
                print(f"post to chat failed: {exc}")
                return 1
            print(f"posted to chat: {msg_id}: {text}")
        if args.speak:
            from livestream_ai.tts import build_tts

            tts = build_tts(cfg.tts.engine)
            out = Path(args.out) / f"reply-{args.approve}.wav"
            out.parent.mkdir(parents=True, exist_ok=True)
            audio = tts.synthesize(text, str(out))
            print(f"spoken audio: {audio.path} ({audio.duration_s:.1f}s, {audio.engine})")
        queue.set_status(args.approve, "approved")
        return 0

    if args.dry_run:
        with Path(args.fixture).open(encoding="utf-8") as f:
            resp = json.load(f)
        messages, token = parse_chat_response(resp)
        print(f"fixture: {len(messages)} messages, nextPageToken={token}")
        state = ChatPollState()
        fresh = state.fresh(messages)
        queued, skipped = _handle_messages(fresh, products, queue, cfg)
        print(f"done: queued={queued} skipped={skipped}")
        return 0

    # Live mode.
    if not cfg.live_chat.enabled:
        print("live_chat.enabled is false in config. Enable it, set")
        print("live_chat.live_chat_id + cart.products_path, then rerun.")
        return 1
    client = YouTubeLiveClient(
        cfg.youtube.client_secrets_path, cfg.youtube.token_path
    )
    state = ChatPollState()
    print(f"polling live chat {cfg.live_chat.live_chat_id} every "
          f"{cfg.live_chat.poll_interval_s}s. Ctrl-C to stop.")
    print("Price/product/buy drafts wait in the queue until approved:")
    print("  python scripts/run_live_chat.py --config config.yaml --approve <id>")
    try:
        while True:
            try:
                resp = client.list_chat_messages(
                    cfg.live_chat.live_chat_id, state.page_token
                )
            except Exception as exc:
                print(f"poll failed (retrying next round): {exc}")
                time.sleep(cfg.live_chat.poll_interval_s)
                continue
            messages, token = parse_chat_response(resp)
            state.page_token = token or state.page_token
            fresh = state.fresh(messages)
            if fresh:
                _handle_messages(
                    fresh,
                    products,
                    queue,
                    cfg,
                    post=lambda text: client.insert_chat_message(
                        cfg.live_chat.live_chat_id, text
                    ),
                )
            time.sleep(cfg.live_chat.poll_interval_s)
    except KeyboardInterrupt:
        print("stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
