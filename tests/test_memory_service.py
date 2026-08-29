"""Primee Memory operations: raw, wiki, outputs, index, changelog, immutability."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode, PrimeeError
from primee.memory.changelog import HEADER, count_entries
from primee.memory.page import parse_page
from primee.memory.service import slugify

from .memory_support import MemoryCase


class RawNoteTests(MemoryCase):
    def test_a_raw_note_is_created_with_an_iso_dated_filename(self):
        result = self.make_raw(title="Kickoff call")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["path"].startswith("raw/2026-08-27-080000-"))
        self.assertTrue(result.data["path"].endswith("kickoff-call.md"))

    def test_a_raw_note_records_its_source_and_capture_time(self):
        result = self.make_raw(source="synthetic feed", source_type="transcript")
        page = self.memory.page_at(result.data["path"])
        self.assertEqual(page.metadata.source, "synthetic feed")
        self.assertEqual(page.metadata.source_type, "transcript")
        self.assertEqual(page.metadata.created, "2026-08-27T08:00:00+03:30")
        self.assertEqual(page.metadata.status, "captured")

    def test_a_raw_note_can_never_be_updated(self):
        result = self.make_raw()
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.write_wiki(
                title="x", body="y", summary="z", slug=result.data["path"], allow_update=True
            )
        self.assertIn(ctx.exception.code, (ErrorCode.VAULT_FILE_MISSING, ErrorCode.VAULT_PATH_REJECTED))

    def test_an_amendment_leaves_the_original_untouched(self):
        original = self.make_raw(title="First capture", body="The pilot runs four weeks.")
        before = self.read_raw_file(original.data["path"])

        amendment = self.memory.amend_raw(
            target=original.data["link_target"],
            body="It is six weeks, not four.",
            summary="Corrects the pilot length.",
            actor="tester",
        )
        self.assertTrue(amendment.ok)
        self.assertTrue(amendment.data["original_unchanged"])
        self.assertEqual(self.read_raw_file(original.data["path"]), before)

    def test_an_amendment_links_back_to_the_original(self):
        original = self.make_raw()
        amendment = self.memory.amend_raw(
            target=original.data["link_target"], body="correction", summary="A correction.",
            actor="tester",
        )
        page = self.memory.page_at(amendment.data["path"])
        self.assertEqual(page.metadata.type, "raw_amendment")
        self.assertEqual(page.metadata.supersedes, original.data["link_target"])
        self.assertIn(f"[[{original.data['link_target']}]]", page.body)
        backlinks = self.memory.backlinks(original.data["link_target"]).data["backlinks"]
        self.assertIn(amendment.data["link_target"], backlinks)

    def test_amending_something_that_is_not_a_raw_note_is_refused(self):
        wiki = self.make_wiki()
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.amend_raw(target=wiki.data["link_target"], body="x", summary="y")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_INPUT)

    def test_amending_a_missing_note_is_refused(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.amend_raw(target="raw/nope", body="x", summary="y")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_MISSING)

    def test_a_raw_note_cannot_be_renamed(self):
        raw = self.make_raw()
        self.make_wiki(title="Anchor", body=f"[[{raw.data['link_target']}]]")
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.rename_page(target=raw.data["link_target"], new_target="raw/renamed")
        self.assertEqual(ctx.exception.code, ErrorCode.PERMISSION_DENIED)


class WikiTests(MemoryCase):
    def test_a_wiki_page_uses_a_stable_slug(self):
        result = self.make_wiki(title="Project Alpha")
        self.assertEqual(result.data["path"], "wiki/project-alpha.md")

    def test_creating_a_wiki_page_twice_is_refused_without_update(self):
        self.make_wiki(title="Topic")
        with self.assertRaises(PrimeeError) as ctx:
            self.make_wiki(title="Topic", body="different")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_EXISTS)
        self.assertIn("update_wiki", ctx.exception.message)

    def test_updating_preserves_created_and_the_id(self):
        first = self.make_wiki(title="Topic", body="v1")
        page_before = self.memory.page_at(first.data["path"])
        second = self.memory.write_wiki(
            title="Topic", body="v2", summary="Updated.", allow_update=True, actor="tester"
        )
        page_after = self.memory.page_at(second.data["path"])
        self.assertEqual(page_after.metadata.created, page_before.metadata.created)
        self.assertEqual(page_after.metadata.id, page_before.metadata.id)
        self.assertIn("v2", page_after.body)

    def test_updating_with_identical_content_does_not_move_updated(self):
        first = self.make_wiki(title="Topic", body="same")
        before = self.memory.page_at(first.data["path"]).metadata.updated
        self.clock = self.clock
        result = self.memory.write_wiki(
            title="Topic", body="same", summary="A synthetic topic.",
            allow_update=True, actor="tester",
        )
        self.assertFalse(result.data["content_changed"])
        self.assertEqual(self.memory.page_at(first.data["path"]).metadata.updated, before)

    def test_updating_a_missing_page_is_refused(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.write_wiki(
                title="Nothing", body="x", summary="y", allow_update=True
            )
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_MISSING)

    def test_a_wiki_page_can_link_to_a_raw_note(self):
        raw = self.make_raw()
        wiki = self.make_wiki(body=f"Fact from [[{raw.data['link_target']}]].")
        links = self.memory.link_graph().links_from(wiki.data["link_target"])
        self.assertEqual([r.status for r in links], ["resolved"])


class OutputTests(MemoryCase):
    def test_output_filenames_begin_with_an_iso_date_and_time(self):
        result = self.make_output(title="Weekly report")
        self.assertEqual(result.data["path"], "outputs/2026-08-27-080000-weekly-report.md")

    def test_a_filename_collision_gets_a_safe_suffix(self):
        first = self.make_output(title="Same title")
        second = self.make_output(title="Same title")
        third = self.make_output(title="Same title")
        self.assertNotEqual(first.data["path"], second.data["path"])
        self.assertTrue(second.data["path"].endswith("-2.md"))
        self.assertTrue(third.data["path"].endswith("-3.md"))

    def test_publishing_never_overwrites_an_existing_output(self):
        first = self.make_output(title="Report", body="original")
        self.make_output(title="Report", body="different")
        self.assertIn("original", self.read_raw_file(first.data["path"]))

    def test_a_final_output_is_left_byte_identical_by_a_revision(self):
        original = self.make_output(title="Report", body="v1", status="final")
        before = self.read_raw_file(original.data["path"])
        revision = self.memory.revise_output(
            target=original.data["link_target"],
            body="v2 corrects v1",
            summary="A correction.",
            actor="tester",
        )
        self.assertTrue(revision.data["original_unchanged"])
        self.assertEqual(self.read_raw_file(original.data["path"]), before)

    def test_a_revision_links_back_and_is_discoverable(self):
        original = self.make_output(title="Report", status="final")
        revision = self.memory.revise_output(
            target=original.data["link_target"], body="corrected", summary="A correction.",
            actor="tester",
        )
        page = self.memory.page_at(revision.data["path"])
        self.assertEqual(page.metadata.supersedes, original.data["link_target"])
        self.assertIn(original.data["link_target"], page.metadata.related)
        self.assertIn(
            revision.data["link_target"],
            self.memory.backlinks(original.data["link_target"]).data["backlinks"],
        )

    def test_revising_a_non_output_is_refused(self):
        wiki = self.make_wiki()
        with self.assertRaises(PrimeeError) as ctx:
            self.memory.revise_output(target=wiki.data["link_target"], body="x", summary="y")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_INPUT)

    def test_an_unknown_output_type_is_refused(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.make_output(output_type="output_novel")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_INPUT)

    def test_slugify_keeps_persian_letters(self):
        self.assertEqual(slugify("برنامه روزانه"), "برنامه-روزانه")
        self.assertEqual(slugify("  !!!  "), "untitled")


class IndexTests(MemoryCase):
    def test_rebuilding_is_deterministic(self):
        self.make_raw()
        self.make_wiki()
        self.make_output()
        first = self.memory.rebuild_index(actor="tester")
        second = self.memory.rebuild_index(actor="tester")
        self.assertEqual(first.data["content_hash"], second.data["content_hash"])

    def test_the_index_lists_every_page_exactly_once(self):
        raw = self.make_raw()
        wiki = self.make_wiki()
        self.memory.rebuild_index(actor="tester")
        content = self.read_raw_file("INDEX.md")
        self.assertEqual(content.count(f"[[{raw.data['link_target']}]]"), 1)
        self.assertEqual(content.count(f"[[{wiki.data['link_target']}]]"), 1)

    def test_each_entry_shows_the_required_columns(self):
        result = self.make_wiki(title="Alpha", summary="A short summary.")
        self.memory.rebuild_index(actor="tester")
        row = [
            line
            for line in self.read_raw_file("INDEX.md").splitlines()
            if result.data["link_target"] in line
        ][0]
        for expected in ("Alpha", "wiki_topic", "A short summary.", "2026-08-27T08:00:00+03:30"):
            self.assertIn(expected, row)

    def test_the_index_is_rebuilt_from_markdown_alone(self):
        self.make_wiki(title="Alpha")
        self.memory.rebuild_index(actor="tester")
        before = self.read_raw_file("INDEX.md")
        (self.vault_root / "INDEX.md").write_text("# destroyed\n", encoding="utf-8")
        self.memory.rebuild_index(actor="tester")
        self.assertEqual(self.read_raw_file("INDEX.md"), before)

    def test_the_index_excludes_root_files_and_non_markdown(self):
        self.storage.create("wiki/notes.txt", "plain text, not a page")
        self.make_wiki(title="Alpha")
        self.memory.rebuild_index(actor="tester")
        content = self.read_raw_file("INDEX.md")
        self.assertNotIn("notes.txt", content)
        self.assertNotIn("[[PRIMEE]]", content)

    def test_broken_pages_are_reported_not_indexed(self):
        self.storage.create("wiki/broken.md", "no frontmatter at all")
        result = self.memory.rebuild_index(actor="tester")
        self.assertEqual(result.data["skipped_broken"], 1)
        self.assertNotIn("broken", self.read_raw_file("INDEX.md"))


class ChangelogTests(MemoryCase):
    def test_every_accepted_write_appends_one_entry(self):
        before = count_entries(self.read_raw_file("CHANGELOG.md"))
        self.make_raw()
        self.make_wiki()
        after = count_entries(self.read_raw_file("CHANGELOG.md"))
        self.assertEqual(after - before, 2)

    def test_an_entry_records_who_asked_and_what_happened(self):
        result = self.make_raw()
        row = [
            line
            for line in self.read_raw_file("CHANGELOG.md").splitlines()
            if result.data["path"] in line
        ][0]
        self.assertIn("create_raw", row)
        self.assertIn("tester", row)
        self.assertIn(result.data["id"], row)

    def test_history_is_never_rewritten(self):
        self.make_raw()
        first = self.read_raw_file("CHANGELOG.md")
        self.make_wiki()
        second = self.read_raw_file("CHANGELOG.md")
        self.assertTrue(second.startswith(first))

    def test_a_tampered_changelog_stops_further_appends(self):
        (self.vault_root / "CHANGELOG.md").write_text("# not the primee header\n", encoding="utf-8")
        with self.assertRaises(PrimeeError) as ctx:
            self.make_raw()
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_CONTENT_REJECTED)
        self.assertIn("preserve history", ctx.exception.message)

    def test_the_changelog_never_records_page_content(self):
        secret_marker = "UNIQUE-BODY-MARKER-12345"
        self.make_raw(body=f"body containing {secret_marker}")
        self.assertNotIn(secret_marker, self.read_raw_file("CHANGELOG.md"))

    def test_the_changelog_records_a_content_hash(self):
        result = self.make_raw()
        row = [
            line
            for line in self.read_raw_file("CHANGELOG.md").splitlines()
            if result.data["path"] in line
        ][0]
        self.assertRegex(row, r"\| [0-9a-f]{16} \|")


class ValidationTests(MemoryCase):
    def test_a_fresh_vault_validates(self):
        self.make_raw()
        self.make_wiki()
        self.assertTrue(self.memory.validate_vault().ok)

    def test_a_missing_folder_is_reported(self):
        import shutil

        shutil.rmtree(self.vault_root / "wiki")
        report = self.memory.validate_vault()
        self.assertFalse(report.ok)
        self.assertEqual(report.data["missing_folders"], ["wiki"])

    def test_duplicate_ids_are_detected(self):
        first = self.make_raw(title="One")
        text = self.read_raw_file(first.data["path"])
        self.storage.create("wiki/clone.md", text.replace("raw_note", "wiki_topic"))
        report = self.memory.validate_vault()
        self.assertFalse(report.ok)

    def test_a_broken_page_is_reported_with_a_sanitized_message(self):
        self.storage.create("raw/bad.md", "---\nid: nope\n---\n\nbody\n")
        report = self.memory.validate_vault()
        self.assertFalse(report.ok)
        self.assertEqual(report.data["broken_pages"][0]["path"], "raw/bad.md")
        self.assertIn("missing required field", report.data["broken_pages"][0]["error"])


if __name__ == "__main__":
    unittest.main()
