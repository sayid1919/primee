"""SKILL.md is untrusted configuration: the parser must be strict and inert."""

from __future__ import annotations

import unittest

from primee.core.errors import FrontmatterError
from primee.core.frontmatter import parse_document


def wrap(body: str) -> str:
    return f"---\n{body}\n---\n\n# Body\n"


class ParseValidTests(unittest.TestCase):
    def test_parses_scalars_lists_and_nested_mappings(self):
        meta, body = parse_document(
            wrap(
                "name: demo\n"
                "version: 1.2.3\n"
                'description: "A demo, with: a colon"\n'
                "triggers:\n"
                "  - one\n"
                '  - "two words"\n'
                "exclusions: []\n"
                "inputs:\n"
                "  - name: period\n"
                "    type: string\n"
                "    required: true\n"
                "persistence:\n"
                "  vault_writes: false\n"
                "  path_prefix: reports\n"
                "count: 7\n"
                "ratio: 0.5\n"
                "nothing: null\n"
            )
        )
        self.assertEqual(meta["name"], "demo")
        self.assertEqual(meta["description"], "A demo, with: a colon")
        self.assertEqual(meta["triggers"], ["one", "two words"])
        self.assertEqual(meta["exclusions"], [])
        self.assertEqual(meta["inputs"][0], {"name": "period", "type": "string", "required": True})
        self.assertEqual(meta["persistence"], {"vault_writes": False, "path_prefix": "reports"})
        self.assertEqual(meta["count"], 7)
        self.assertEqual(meta["ratio"], 0.5)
        self.assertIsNone(meta["nothing"])
        self.assertEqual(body.strip(), "# Body")

    def test_strips_comments_but_not_inside_quotes(self):
        meta, _ = parse_document(wrap('a: value # trailing\nb: "hash # inside"\n'))
        self.assertEqual(meta["a"], "value")
        self.assertEqual(meta["b"], "hash # inside")

    def test_keeps_persian_text_and_zwnj(self):
        meta, _ = parse_document(wrap('title: "برنامه‌ روزانه"\n'))
        self.assertIn("برنامه", meta["title"])


class ParseMalformedTests(unittest.TestCase):
    def assert_rejected(self, body: str, *, wrapped: bool = True):
        text = wrap(body) if wrapped else body
        with self.assertRaises(FrontmatterError):
            parse_document(text)

    def test_rejects_missing_opening_delimiter(self):
        self.assert_rejected("# just markdown\n", wrapped=False)

    def test_rejects_unclosed_frontmatter(self):
        self.assert_rejected("---\nname: x\n", wrapped=False)

    def test_rejects_unterminated_quote(self):
        self.assert_rejected('triggers:\n  - "never closed\n')

    def test_rejects_tab_indentation(self):
        self.assert_rejected("persistence:\n\tvault_writes: false\n")

    def test_rejects_duplicate_keys(self):
        self.assert_rejected("name: a\nname: b\n")

    def test_rejects_yaml_tags(self):
        self.assert_rejected('triggers: !!python/object/apply:os.system ["echo pwned"]\n')

    def test_rejects_anchors_and_aliases(self):
        self.assert_rejected("base: &anchor value\nother: *anchor\n")

    def test_rejects_flow_collections(self):
        self.assert_rejected("triggers: [one, two]\n")

    def test_rejects_block_scalars(self):
        self.assert_rejected("description: |\n  multi\n  line\n")

    def test_rejects_nul_byte(self):
        self.assert_rejected("name: a\x00b\n")

    def test_rejects_bidi_override_in_value(self):
        self.assert_rejected('name: "a‮b"\n')

    def test_rejects_oversized_document(self):
        self.assert_rejected("---\n" + "k: v\n" * 20000 + "---\n", wrapped=False)

    def test_rejects_unexpected_indentation(self):
        self.assert_rejected("a: 1\n    b: 2\n")


class InertnessTests(unittest.TestCase):
    def test_parser_returns_only_plain_python_types(self):
        meta, _ = parse_document(
            wrap("a: 1\nb: text\nc:\n  - x\nd:\n  e: true\n")
        )
        allowed = (str, int, float, bool, type(None), list, dict)
        stack = [meta]
        while stack:
            item = stack.pop()
            self.assertIsInstance(item, allowed)
            if isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)


if __name__ == "__main__":
    unittest.main()
