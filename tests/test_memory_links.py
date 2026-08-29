"""Wikilinks, backlinks, dangling and ambiguous links, and safe renaming."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode, PrimeeError
from primee.memory.links import (
    AMBIGUOUS,
    DANGLING,
    RESOLVED,
    LinkGraph,
    extract_links,
    normalize_target,
    plan_rename,
    rewrite_links,
)

from .memory_support import MemoryCase


class ParsingTests(unittest.TestCase):
    def test_extracts_a_plain_wikilink(self):
        links = extract_links("See [[wiki/topic-name]] for details.")
        self.assertEqual([l.target for l in links], ["wiki/topic-name"])
        self.assertEqual(links[0].display, "")

    def test_extracts_a_wikilink_with_display_text(self):
        links = extract_links("See [[wiki/topic|the topic]].")
        self.assertEqual(links[0].target, "wiki/topic")
        self.assertEqual(links[0].display, "the topic")

    def test_de_duplicates_repeated_links(self):
        links = extract_links("[[a]] then [[a]] again and [[b]]")
        self.assertEqual([l.target for l in links], ["a", "b"])

    def test_ignores_ordinary_brackets_and_markdown_links(self):
        self.assertEqual(extract_links("[a](b) and [single] and [[]]"), [])

    def test_normalizes_extensions_and_separators(self):
        self.assertEqual(normalize_target("./wiki/topic.md"), "wiki/topic")
        self.assertEqual(normalize_target("wiki\\topic"), "wiki/topic")
        self.assertEqual(normalize_target("  /wiki/topic/  "), "wiki/topic")

    def test_handles_persian_link_targets(self):
        links = extract_links("[[wiki/برنامه-روزانه]]")
        self.assertEqual(links[0].target, "wiki/برنامه-روزانه")

    def test_a_link_spanning_lines_is_not_matched(self):
        self.assertEqual(extract_links("[[wiki/\ntopic]]"), [])


class GraphTests(unittest.TestCase):
    def graph(self):
        return LinkGraph.build(
            {
                "wiki/alpha": ("Alpha", "links to [[wiki/beta]] and [[nowhere]]"),
                "wiki/beta": ("Beta", "back to [[wiki/alpha]]"),
                "raw/beta": ("Raw beta", "mentions [[beta]]"),
                "outputs/2026-01-01-report": ("Report", "based on [[wiki/alpha]]"),
            }
        )

    def test_forward_links_resolve(self):
        resolutions = self.graph().links_from("wiki/beta")
        self.assertEqual([r.status for r in resolutions], [RESOLVED])
        self.assertEqual(resolutions[0].resolved_to, "wiki/alpha")

    def test_backlinks_find_every_source(self):
        self.assertEqual(
            self.graph().backlinks("wiki/alpha"),
            ["outputs/2026-01-01-report", "wiki/beta"],
        )

    def test_backlinks_of_an_unlinked_page_is_empty(self):
        self.assertEqual(self.graph().backlinks("raw/beta"), [])

    def test_dangling_links_are_reported_not_removed(self):
        dangling = self.graph().dangling()
        self.assertEqual([(d.source, d.target) for d in dangling], [("wiki/alpha", "nowhere")])
        self.assertEqual(dangling[0].status, DANGLING)

    def test_ambiguous_short_links_are_reported(self):
        ambiguous = self.graph().ambiguous()
        self.assertEqual(ambiguous[0].target, "beta")
        self.assertEqual(ambiguous[0].status, AMBIGUOUS)
        self.assertEqual(list(ambiguous[0].candidates), ["raw/beta", "wiki/beta"])

    def test_duplicate_page_names_are_reported(self):
        self.assertEqual(self.graph().duplicate_basenames(), {"beta": ["raw/beta", "wiki/beta"]})

    def test_a_short_unambiguous_link_resolves(self):
        graph = LinkGraph.build({"wiki/only": ("Only", ""), "wiki/a": ("A", "[[only]]")})
        self.assertEqual(graph.links_from("wiki/a")[0].resolved_to, "wiki/only")

    def test_the_summary_counts_everything(self):
        summary = self.graph().summary()
        self.assertEqual(summary["pages"], 4)
        self.assertEqual(summary["links"], 5)
        self.assertEqual(summary["resolved"], 3)


class RewriteTests(unittest.TestCase):
    def test_rewriting_preserves_display_text(self):
        body, changed = rewrite_links("[[old|Label]] and [[old]]", "old", "new")
        self.assertEqual(changed, 2)
        self.assertIn("[[new|Label]]", body)
        self.assertIn("[[new]]", body)

    def test_rewriting_leaves_other_links_alone(self):
        body, changed = rewrite_links("[[other]] [[old]]", "old", "new")
        self.assertEqual(changed, 1)
        self.assertIn("[[other]]", body)


class RenameTests(unittest.TestCase):
    def graph(self):
        return LinkGraph.build(
            {
                "wiki/alpha": ("Alpha", ""),
                "wiki/beta": ("Beta", "[[wiki/alpha]]"),
                "raw/gamma": ("Gamma", "[[wiki/alpha]]"),
            }
        )

    def test_a_safe_rename_lists_the_pages_to_update(self):
        affected, refusal = plan_rename(self.graph(), "wiki/alpha", "wiki/renamed")
        self.assertIsNone(refusal)
        self.assertEqual(affected, ["raw/gamma", "wiki/beta"])

    def test_renaming_onto_an_existing_page_is_refused(self):
        _affected, refusal = plan_rename(self.graph(), "wiki/alpha", "wiki/beta")
        self.assertIn("already exists", refusal)

    def test_renaming_a_missing_page_is_refused(self):
        _affected, refusal = plan_rename(self.graph(), "wiki/nope", "wiki/new")
        self.assertIn("no page", refusal)

    def test_a_rename_that_would_create_an_ambiguous_name_is_refused(self):
        graph = LinkGraph.build({"wiki/alpha": ("A", ""), "raw/target": ("T", "")})
        _affected, refusal = plan_rename(graph, "wiki/alpha", "wiki/target")
        self.assertIn("ambiguous", refusal)

    def test_renaming_to_the_same_name_is_refused(self):
        _affected, refusal = plan_rename(self.graph(), "wiki/alpha", "wiki/alpha")
        self.assertIn("identical", refusal)


class VaultLinkTests(MemoryCase):
    def test_validate_links_reports_a_broken_link(self):
        self.make_wiki(title="Alpha", body="points at [[wiki/does-not-exist]]")
        report = self.memory.validate_links()
        self.assertFalse(report.ok)
        self.assertEqual(report.data["dangling"][0]["target"], "wiki/does-not-exist")
        self.assertIn("never removed", report.message)

    def test_a_broken_link_is_left_in_the_file(self):
        result = self.make_wiki(title="Alpha", body="points at [[wiki/does-not-exist]]")
        self.memory.validate_links()
        self.assertIn("[[wiki/does-not-exist]]", self.read_raw_file(result.data["path"]))

    def test_validate_links_passes_when_everything_resolves(self):
        raw = self.make_raw()
        self.make_wiki(title="Alpha", body=f"from [[{raw.data['link_target']}]]")
        self.assertTrue(self.memory.validate_links().ok)

    def test_backlinks_through_the_service(self):
        raw = self.make_raw()
        wiki = self.make_wiki(title="Alpha", body=f"from [[{raw.data['link_target']}]]")
        report = self.memory.backlinks(raw.data["link_target"])
        self.assertTrue(report.data["exists"])
        self.assertEqual(report.data["backlinks"], [wiki.data["link_target"]])

    def test_backlinks_of_an_unknown_target_reports_it_does_not_exist(self):
        report = self.memory.backlinks("wiki/ghost")
        self.assertFalse(report.data["exists"])
        self.assertEqual(report.data["backlinks"], [])

    def test_renaming_a_wiki_page_repoints_its_links(self):
        self.make_wiki(title="Alpha", slug="alpha", body="content")
        self.make_wiki(title="Beta", slug="beta", body="see [[wiki/alpha]]")
        result = self.memory.rename_page(target="wiki/alpha", new_target="wiki/alpha-renamed",
                                         actor="tester")
        self.assertTrue(result.ok)
        self.assertIn("[[wiki/alpha-renamed]]", self.read_raw_file("wiki/beta.md"))
        self.assertTrue((self.vault_root / "wiki" / "alpha-renamed.md").is_file())

    def test_an_ambiguous_rename_stops_and_asks(self):
        self.make_wiki(title="Alpha", slug="alpha")
        self.make_wiki(title="Target", slug="target")
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.rename_page(target="wiki/alpha", new_target="wiki/target")
        self.assertEqual(ctx.exception.code, ErrorCode.AMBIGUOUS_REQUEST)


if __name__ == "__main__":
    unittest.main()
