"""The fictional template Vault stays valid, linked and free of personal data."""

from __future__ import annotations

import re
import unittest

from primee.memory.index_builder import IndexEntry, build_index
from primee.memory.page import parse_page
from primee.memory.schema import CHANGELOG_FILE, CONTENT_FOLDERS, INDEX_FILE, PRIMEE_FILE

from . import REPO_ROOT

TEMPLATE = REPO_ROOT / "templates" / "vault"


def pages():
    for folder in CONTENT_FOLDERS:
        for path in sorted((TEMPLATE / folder).rglob("*.md")):
            yield path.relative_to(TEMPLATE).as_posix(), path.read_text(encoding="utf-8")


class StructureTests(unittest.TestCase):
    def test_the_template_exists_with_all_three_folders(self):
        for folder in CONTENT_FOLDERS:
            self.assertTrue((TEMPLATE / folder).is_dir(), msg=folder)

    def test_the_template_has_all_three_root_files(self):
        for name in (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE):
            self.assertTrue((TEMPLATE / name).is_file(), msg=name)

    def test_the_template_has_no_claude_file(self):
        for path in TEMPLATE.rglob("*"):
            self.assertNotIn("claude", path.name.lower())

    def test_it_demonstrates_one_of_each_content_kind(self):
        kinds = {path.split("/")[0] for path, _ in pages()}
        self.assertEqual(kinds, set(CONTENT_FOLDERS))

    def test_output_filenames_start_with_an_iso_date(self):
        for path, _ in pages():
            if path.startswith("outputs/"):
                name = path.split("/")[-1]
                self.assertRegex(name, r"^\d{4}-\d{2}-\d{2}-\d{6}-")


class ValidityTests(unittest.TestCase):
    def test_every_template_page_parses_and_validates(self):
        count = 0
        for path, text in pages():
            parse_page(text, relative_path=path)
            count += 1
        self.assertGreaterEqual(count, 3)

    def test_page_ids_are_unique(self):
        ids = [parse_page(t, relative_path=p).metadata.id for p, t in pages()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_wikilink_resolves(self):
        from primee.memory.links import LinkGraph

        graph = LinkGraph.build(
            {
                p[:-3]: (parse_page(t, relative_path=p).metadata.title,
                         parse_page(t, relative_path=p).body)
                for p, t in pages()
            }
        )
        summary = graph.summary()
        self.assertEqual(summary["dangling"], [])
        self.assertEqual(summary["ambiguous"], [])
        self.assertGreater(summary["resolved"], 0)

    def test_the_index_matches_a_fresh_rebuild(self):
        entries = []
        for path, text in pages():
            page = parse_page(text, relative_path=path)
            entries.append(
                IndexEntry(
                    link_target=page.link_target,
                    title=page.metadata.title,
                    type=page.metadata.type,
                    summary=page.metadata.summary,
                    updated=page.metadata.updated,
                )
            )
        stored = (TEMPLATE / INDEX_FILE).read_text(encoding="utf-8")
        generated_at = re.search(r"Generated at: (\S+)", stored).group(1)
        self.assertEqual(build_index(entries, generated_at=generated_at), stored)

    def test_the_wiki_page_links_to_the_raw_note(self):
        wiki = (TEMPLATE / "wiki" / "lighthouse-project.md").read_text(encoding="utf-8")
        self.assertIn("[[raw/", wiki)

    def test_the_output_links_to_the_wiki_page(self):
        outputs = list((TEMPLATE / "outputs").glob("*.md"))
        self.assertTrue(any("[[wiki/" in p.read_text(encoding="utf-8") for p in outputs))

    def test_the_wiki_page_separates_facts_from_interpretations(self):
        wiki = (TEMPLATE / "wiki" / "lighthouse-project.md").read_text(encoding="utf-8")
        self.assertIn("## Facts", wiki)
        self.assertIn("## Interpretations", wiki)
        self.assertIn("## Open conflicts", wiki)

    def test_a_plan_candidates_page_is_provided(self):
        text = (TEMPLATE / "wiki" / "plan-candidates.md").read_text(encoding="utf-8")
        for field in ("reason:", "expected_outcome:", "completion_condition:"):
            self.assertIn(field, text)


class NoPersonalDataTests(unittest.TestCase):
    """The template is fictional. These checks are deliberately blunt."""

    def all_text(self) -> str:
        return "\n".join(
            path.read_text(encoding="utf-8")
            for path in TEMPLATE.rglob("*")
            if path.is_file()
        )

    def test_no_email_address_appears(self):
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", self.all_text()))

    def test_no_credential_shape_appears(self):
        text = self.all_text()
        for pattern in (
            r"-----BEGIN[^-]*PRIVATE KEY",
            r"\bsk-[A-Za-z0-9]{20,}",
            r"(?i)\b(password|api[_-]?key|token|secret)\s*[:=]\s*\S",
        ):
            self.assertIsNone(re.search(pattern, text), msg=pattern)

    def test_no_long_digit_run_appears(self):
        self.assertIsNone(re.search(r"\b\d{9,}\b", self.all_text()))

    def test_no_absolute_machine_path_appears(self):
        text = self.all_text()
        for pattern in (r"[A-Za-z]:\\Users\\", r"/home/[a-z]", r"/Users/[a-z]"):
            self.assertIsNone(re.search(pattern, text), msg=pattern)

    def test_the_content_declares_itself_fictional(self):
        self.assertIn("ictional", self.all_text())

    def test_the_template_readme_warns_it_is_not_a_real_vault(self):
        readme = (REPO_ROOT / "templates" / "README.md").read_text(encoding="utf-8")
        self.assertIn("fictional", readme.lower())
        self.assertIn("not your Vault", readme)


class PrimeeRulesTests(unittest.TestCase):
    def test_the_rules_file_explains_the_three_folders(self):
        text = (TEMPLATE / PRIMEE_FILE).read_text(encoding="utf-8")
        for expected in ("`raw/`", "`wiki/`", "`outputs/`"):
            self.assertIn(expected, text)

    def test_the_rules_file_is_honest_about_encryption(self):
        text = (TEMPLATE / PRIMEE_FILE).read_text(encoding="utf-8")
        self.assertIn("not encrypted", text)
        self.assertNotIn("securely encrypted", text)

    def test_the_rules_file_says_it_does_not_enforce_anything(self):
        text = (TEMPLATE / PRIMEE_FILE).read_text(encoding="utf-8")
        self.assertIn("never creates a `CLAUDE.md`", text)


if __name__ == "__main__":
    unittest.main()
