"""Product catalog (cart) for livestream-ai Phase 2.

The catalog is a local YAML file owned by the operator. It is the
single source of truth for what the avatar may say about a product:
name, price in VND, cart/affiliate link, keywords viewers use, and a
short note. The avatar never invents a price or a link: a reply is
built only from a catalog entry, and a product with no entry gets no
price answer.

Affiliate tracking (the team's own system): every link in this
catalog goes through ztr.info, the team's affiliate tracking hub
(Worker `z-tracker`, live at https://z-tracker.7en.workers.dev/
until the ztr.info/* route is attached). Two forms:
  - `/goto/?url=<product URL>&s=<site>&ref=<placement>` - wrap a
    Shopee/Lazada product URL, no pre-registration needed. The
    Worker redirects (302), logs the click, and attaches the
    operator's affiliate_id + sub_id from /admin config.
  - `/g/<code>` - short link created in /admin, same redirect.
The sub_id carries the source + placement (Shopee:
`{site}_{placement}_{click_id}`, Lazada: `{site}_{click_id}`), so an
order from the live attributes to the live. For livestream-ai use
s=livestream-ai and ref=live-chat (or the live session name).
IMPORTANT: the affiliate_id values in /admin are still placeholders
- the operator must enter the real Shopee + Lazada affiliate IDs in
/admin before a live session, or clicks log but no commission
attaches. The Worker never invents an affiliate id.

The link may point at any shop the operator uses (Shopee, Lazada,
YouTube Shopping, or the operator's own store). The catalog does
not call any shop API and does not fetch prices: the operator edits
the YAML when a price or link changes, so a wrong price is the
operator's edit, visible in git, not a silent scrape.

YouTube Shopping native tagging is NOT done by this module: the study
(rev2 section 5.5) found no public API to tag/pin a product inside a
live stream. Tagging stays a manual step in YouTube Studio. What this
module does instead, and what works on every platform: show the
product on screen (OBS overlay) and post its link in live chat.

File format (products.example.yaml shows a full example):

  products:
    - id: P01
      name: "Den ban hoc LED"
      price_vnd: 189000
      link: "https://example.invalid/shop/p01"
      keywords: ["den", "den ban hoc", "led"]
      note: "Anh sang 3 che do, co chan sac USB-C."
      in_stock: true
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class CatalogError(ValueError):
    """Raised when the catalog file is missing or invalid."""


@dataclass
class Product:
    id: str
    name: str
    price_vnd: int
    link: str
    keywords: list[str] = field(default_factory=list)
    note: str = ""
    in_stock: bool = True
    # Where the product is sold: "shopee" | "lazada" | "" (unknown).
    # Shown on the card and named in buy replies so viewers know
    # which app to open. "" keeps old catalogs working.
    platform: str = ""


PLATFORM_LABELS = {"shopee": "Shopee", "lazada": "Lazada"}


def format_price_vnd(price_vnd: int) -> str:
    """189000 -> '189.000 đ'."""
    return f"{price_vnd:,}".replace(",", ".") + " đ"


def load_products(path: str | Path) -> list[Product]:
    """Load and validate a products YAML file."""
    path = Path(path)
    if not path.is_file():
        raise CatalogError(f"products file not found: {path}")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("products"), list):
        raise CatalogError("products file must contain a 'products' list")
    products: list[Product] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw["products"]):
        if not isinstance(item, dict):
            raise CatalogError(f"products[{i}] must be a mapping")
        pid = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        link = str(item.get("link", "")).strip()
        if not pid:
            raise CatalogError(f"products[{i}] is missing 'id'")
        if pid in seen_ids:
            raise CatalogError(f"duplicate product id: {pid}")
        seen_ids.add(pid)
        if not name:
            raise CatalogError(f"product {pid} is missing 'name'")
        if not link.startswith(("http://", "https://")):
            raise CatalogError(
                f"product {pid} link must start with http:// or https://"
            )
        try:
            price = int(item.get("price_vnd", 0))
        except (TypeError, ValueError) as exc:
            raise CatalogError(f"product {pid} price_vnd is not a number") from exc
        if price < 0:
            raise CatalogError(f"product {pid} price_vnd must be >= 0")
        keywords = item.get("keywords") or []
        if not isinstance(keywords, list) or not all(
            isinstance(k, str) for k in keywords
        ):
            raise CatalogError(f"product {pid} keywords must be a list of strings")
        platform = str(item.get("platform", "")).strip().lower()
        if platform not in ("", "shopee", "lazada"):
            raise CatalogError(
                f"product {pid} platform must be shopee, lazada or empty"
            )
        products.append(
            Product(
                id=pid,
                name=name,
                price_vnd=price,
                link=link,
                keywords=[k.strip().lower() for k in keywords if k.strip()],
                note=str(item.get("note", "")).strip(),
                in_stock=bool(item.get("in_stock", True)),
                platform=platform,
            )
        )
    if not products:
        raise CatalogError("products list is empty")
    return products


def find_product(text: str, products: list[Product]) -> Product | None:
    """Best product match for a viewer comment, or None.

    Scores: exact product id (e.g. 'P01') wins, then keyword hits,
    then name tokens. Returns None when nothing matches.
    """
    lowered = text.lower()
    for p in products:
        if p.id.lower() in lowered:
            return p
    best: Product | None = None
    best_score = 0
    for p in products:
        score = 0
        for kw in p.keywords:
            if kw and kw in lowered:
                # Longer keywords are more specific, weigh them more.
                score += len(kw)
        for token in p.name.lower().split():
            # Only long tokens count: short words like "gia" (price)
            # appear in almost every question and would outvote the
            # real keyword match. Keywords carry short matches instead.
            if len(token) > 3 and token in lowered:
                score += 2
        if score > best_score:
            best_score = score
            best = p
    return best if best_score > 0 else None


def product_card_text(product: Product) -> str:
    """One-line overlay text: 'Den ban hoc LED - 189.000 đ'."""
    stock = "" if product.in_stock else " (het hang)"
    return f"{product.name} - {format_price_vnd(product.price_vnd)}{stock}"


def render_product_card_html(product: Product) -> str:
    """Static HTML card for an OBS Browser source.

    No external assets, no JavaScript, no tracking. The operator adds
    it as a Browser source pointing at the generated file, or copies
    the markup into their own overlay scene.
    """
    name = html.escape(product.name)
    price = html.escape(format_price_vnd(product.price_vnd))
    link = html.escape(product.link, quote=True)
    note = html.escape(product.note)
    stock = "Con hang" if product.in_stock else "Het hang"
    platform = PLATFORM_LABELS.get(product.platform, "")
    platform_line = f" - {html.escape(platform)}" if platform else ""
    return (
        "<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\">"
        "<style>body{margin:0;font-family:sans-serif;background:transparent}"
        ".card{background:rgba(0,0,0,.78);color:#fff;border-radius:12px;"
        "padding:16px 20px;max-width:520px}"
        ".name{font-size:26px;font-weight:700}"
        ".price{font-size:30px;color:#ffd166;margin-top:6px}"
        ".note{font-size:16px;opacity:.9;margin-top:6px}"
        ".stock{font-size:14px;opacity:.8;margin-top:6px}"
        ".link{font-size:14px;opacity:.8;margin-top:4px;word-break:break-all}"
        "</style></head><body><div class=\"card\">"
        f"<div class=\"name\">{name}</div>"
        f"<div class=\"price\">{price}{platform_line}</div>"
        f"<div class=\"note\">{note}</div>"
        f"<div class=\"stock\">{stock} - San pham AI gioi thieu, kiem tra gia truoc khi dat</div>"
        f"<div class=\"link\">{link}</div>"
        "</div></body></html>"
    )


def write_product_card_html(product: Product, out_path: str | Path) -> str:
    """Write the card HTML to a file. Returns the path as a string."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_product_card_html(product), encoding="utf-8")
    return str(out)
