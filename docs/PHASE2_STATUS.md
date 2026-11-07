# livestream-ai - Phase 2: cart + live chat interaction (2026-11-07)

## Scope decided

- **YouTube-first.** Phase 1 already chose YouTube (only platform with
  a public live chat API). Phase 2 keeps that choice.
- **Cart = local catalog + OBS overlay + chat link.** The operator owns
  `products.yaml` (name, price, link, keywords). The avatar only
  repeats catalog data, never fetches or invents a price.
- **Native YouTube Shopping tagging is NOT in this phase.** The study
  (rev2 section 5.5) found no public API to tag/pin a product inside
  a live stream. Tagging stays manual in YouTube Studio.
- **Shopee / TikTok live comment reading is NOT in this phase.**
  Shopee: one endpoint seen only in a job posting, needs Partner
  rights. TikTok Shop Partner API covers product/order/performance,
  not live chat control. Catalog links still work there (post the
  link, show the card), but live comment reading there is Phase 3
  behind partner approval.
- **Human-in-the-loop (study Pha 4).** Price / product / buy drafts
  wait in `logs/chat-queue.jsonl` until the operator approves them.
  Greetings auto-reply only when `live_chat.auto_reply_greeting`
  is true (default false). No LLM call: replies are templates from
  catalog data, so no invented price or discount.

## What was built

- `src/livestream_ai/catalog.py` - load/validate products YAML,
  `find_product` by id/keyword/name, `format_price_vnd`,
  OBS product card HTML (no JS, no tracking) + one-line overlay text.
- `src/livestream_ai/live_chat.py` - parse `liveChatMessages.list`,
  classify (price / buy / product / greeting / spam / other),
  draft template replies, `ApprovalQueue` (JSONL, pending/approved/
  rejected), `ChatPollState` dedup by message id.
- `src/livestream_ai/youtube_live.py` - added `build_chat_insert_body`
  (max 200 chars) + `YouTubeLiveClient.insert_chat_message`.
- `src/livestream_ai/config.py` - new `[cart]` and `[live_chat]`
  sections, validated (poll >= 5 s, reply 50-500 chars, chat needs
  a catalog when enabled).
- `scripts/run_live_chat.py` - `--dry-run` (fixture), live poll loop,
  `--list-pending`, `--approve <id>` (posts to chat, optional
  `--speak` synthesizes the reply with TTS), `--reject <id>`.
- `products.example.yaml` - 2 example products, example.invalid links.
- `tests/test_catalog.py` (9 tests) + `tests/test_live_chat.py`
  (17 tests) + `tests/fixtures/chat-sample.json` (5 messages:
  price, buy, greeting, spam link, off-topic).

## Test

| Test | Result |
|---|---|
| Full suite `python -m unittest discover -s tests` | **PASS 66/66** (42 Phase 1 + 24 Phase 2 new), exit 0 |
| Dry run `run_live_chat.py --dry-run` with fixture + products-test.yaml | PASS: 5 messages -> 3 queued (price, buy, greeting), 2 skipped (spam link, off-topic); `--approve m1 --speak` wrote mock WAV 1.3 s, queue left 2 pending |
| Live poll / live post / live TTS speak | NOT RUN - needs operator OAuth + verified channel + live broadcast |
| Shopee / TikTok live comment APIs | NOT RUN - needs Partner rights, out of scope for Phase 2 |

## How the operator runs it (live)

1. Copy `products.example.yaml` to `products.yaml`, replace the
   example products with real ones (real prices, real shop links).
2. In `config.yaml`: set `cart.products_path: "products.yaml"`,
   `live_chat.enabled: true`, `live_chat.live_chat_id` (from
   `liveBroadcasts.list`, `snippet.liveChatId`).
3. Start OBS, add a Text source named "Product Card" and a Browser
   source pointing at `out/chat/product-card.html` (written when a
   product is mentioned).
4. Run: `python scripts/run_live_chat.py --config config.yaml`
   (polls every 10 s, prints new comments + drafts).
5. Approve a reply: `python scripts/run_live_chat.py --config
   config.yaml --approve <message-id>` (add `--speak` to also
   synthesize it so the avatar can say it).
6. Check the queue any time: `--list-pending`.

## Quota

Poll 10 s = 5 units/call, about 1,800 units/hour. Insert costs 20
units per posted reply. Default project quota is 10,000 units/day,
so one live session of a few hours fits; a full-day live needs a
longer poll interval or a quota increase.

## Still open

- LLM draft replies (template today; LLM must keep the same rule:
  draft from catalog data, approval before speaking).
- Shopee Livestream Management Partner approval + TikTok Shop live
  comment path (Phase 3).
- Real live test on the operator's verified channel (needs Zen's
  YouTube channel + Google Cloud project, already open from Phase 1).
- Latency measurement: study target is under 10 s from comment to
  avatar speaking (unverified threshold, needs a real live run).
