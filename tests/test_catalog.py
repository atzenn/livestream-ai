"""Product catalog tests (offline, no network)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from livestream_ai.catalog import (
    CatalogError,
    find_product,
    format_price_vnd,
    load_products,
    product_card_text,
    render_product_card_html,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestCatalog(unittest.TestCase):
    def setUp(self):
        self.products = load_products(FIXTURES / "products-test.yaml")

    def test_load_example(self):
        self.assertEqual(len(self.products), 2)
        self.assertEqual(self.products[0].id, "P01")
        self.assertEqual(self.products[0].price_vnd, 189000)
        self.assertEqual(self.products[0].platform, "shopee")
        self.assertEqual(self.products[1].platform, "lazada")

    def test_bad_platform_rejected(self):
        bad = FIXTURES / "products-bad-platform.yaml"
        bad.write_text(
            "products:\n  - id: P99\n    name: \"Bad\"\n    price_vnd: 1000\n"
            "    platform: tiktok\n"
            "    link: \"https://example.invalid/x\"\n",
            encoding="utf-8",
        )
        try:
            with self.assertRaises(CatalogError):
                load_products(bad)
        finally:
            bad.unlink()

    def test_format_price(self):
        self.assertEqual(format_price_vnd(189000), "189.000 đ")
        self.assertEqual(format_price_vnd(99000), "99.000 đ")
        self.assertEqual(format_price_vnd(0), "0 đ")

    def test_find_by_keyword(self):
        p = find_product("den ban hoc gia bao nhieu?", self.products)
        self.assertIsNotNone(p)
        self.assertEqual(p.id, "P01")

    def test_find_by_id(self):
        p = find_product("lay P02 nhe", self.products)
        self.assertIsNotNone(p)
        self.assertEqual(p.id, "P02")

    def test_find_none(self):
        self.assertIsNone(find_product("hom nay troi dep nhi", self.products))

    def test_missing_file(self):
        with self.assertRaises(CatalogError):
            load_products(FIXTURES / "no-such-products.yaml")

    def test_bad_link_rejected(self):
        bad = FIXTURES / "products-bad.yaml"
        bad.write_text(
            "products:\n  - id: P99\n    name: \"Bad\"\n    price_vnd: 1000\n"
            "    link: \"ftp://example.invalid/x\"\n",
            encoding="utf-8",
        )
        try:
            with self.assertRaises(CatalogError):
                load_products(bad)
        finally:
            bad.unlink()

    def test_duplicate_id_rejected(self):
        dup = FIXTURES / "products-dup.yaml"
        dup.write_text(
            "products:\n"
            "  - id: P01\n    name: \"A\"\n    price_vnd: 1000\n"
            "    link: \"https://example.invalid/a\"\n"
            "  - id: P01\n    name: \"B\"\n    price_vnd: 2000\n"
            "    link: \"https://example.invalid/b\"\n",
            encoding="utf-8",
        )
        try:
            with self.assertRaises(CatalogError):
                load_products(dup)
        finally:
            dup.unlink()

    def test_card_text_and_html(self):
        p = self.products[0]
        self.assertEqual(product_card_text(p), "Den ban hoc LED - 189.000 đ")
        html = render_product_card_html(p)
        self.assertIn("Den ban hoc LED", html)
        self.assertIn("189.000", html)
        self.assertIn("Shopee", html)
        self.assertIn("https://example.invalid/shop/den-ban-hoc-led", html)
        self.assertNotIn("<script", html)


if __name__ == "__main__":
    unittest.main()
