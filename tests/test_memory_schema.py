"""The Primee Memory page schema: required fields, vocabularies, timestamps."""

from __future__ import annotations

import unittest

from primee.memory.page import parse_page, render_page
from primee.memory.schema import (
    FINAL_STATUSES,
    IMMUTABLE_TYPES,
    OUTPUT_TYPES,
    PAGE_TYPES,
    REQUIRED_FIELDS,
    SCHEMA_VERSION,
    SENSITIVITY_VALUES,
    SchemaError,
    build_metadata,
    check_type_matches_path,
    generate_id,
    normalize_tags,
    validate_timestamp,
)

VALID = {
    "id": "raw-20260827-0123456789",
    "schema_version": SCHEMA_VERSION,
    "title": "A capture",
    "type": "raw_note",
    "tags": ["one", "two"],
    "created": "2026-08-27T08:00:00+03:30",
    "updated": "2026-08-27T08:00:00+03:30",
    "summary": "One line of description.",
}


def meta(**overrides):
    data = dict(VALID)
    data.update(overrides)
    return build_metadata(data, relative_path="raw/a.md")


class RequiredFieldTests(unittest.TestCase):
    def test_a_valid_page_validates(self):
        page = meta()
        self.assertEqual(page.id, VALID["id"])
        self.assertEqual(page.tags, ("one", "two"))
        self.assertEqual(page.sensitivity, "private")

    def test_every_required_field_is_enforced(self):
        for field in REQUIRED_FIELDS:
            data = dict(VALID)
            data.pop(field)
            with self.assertRaises(SchemaError, msg=f"{field} should be required"):
                build_metadata(data, relative_path="raw/a.md")

    def test_missing_metadata_is_never_invented(self):
        data = dict(VALID)
        data.pop("summary")
        with self.assertRaises(SchemaError) as ctx:
            build_metadata(data, relative_path="raw/a.md")
        self.assertIn("summary", ctx.exception.message)

    def test_rejects_an_unknown_schema_version(self):
        with self.assertRaises(SchemaError):
            meta(schema_version=99)

    def test_rejects_a_non_integer_schema_version(self):
        with self.assertRaises(SchemaError):
            meta(schema_version="1")


class TypeTests(unittest.TestCase):
    def test_rejects_an_unknown_type(self):
        with self.assertRaises(SchemaError):
            meta(type="diary_entry")

    def test_type_must_match_the_folder(self):
        with self.assertRaises(SchemaError) as ctx:
            build_metadata({**VALID, "type": "wiki_topic"}, relative_path="raw/a.md")
        self.assertIn("belongs in wiki", ctx.exception.message)

    def test_every_declared_type_has_a_folder_rule(self):
        for name, spec in PAGE_TYPES.items():
            self.assertIn(spec.folder, ("", "raw", "wiki", "outputs"), msg=name)

    def test_raw_types_are_marked_immutable(self):
        self.assertEqual(IMMUTABLE_TYPES, {"raw_note", "raw_amendment"})

    def test_final_statuses_apply_to_outputs(self):
        page = build_metadata(
            {**VALID, "type": "output_report", "status": "final", "id": "out-20260827-0123456789"},
            relative_path="outputs/a.md",
        )
        self.assertTrue(page.is_final_output)
        self.assertEqual(FINAL_STATUSES, {"final", "shipped"})

    def test_check_type_matches_path_accepts_root_types(self):
        check_type_matches_path("vault_index", "INDEX.md")


class TimestampTests(unittest.TestCase):
    def test_requires_a_timezone(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_timestamp("2026-08-27T08:00:00", "created")
        self.assertIn("timezone", ctx.exception.message)

    def test_rejects_a_non_iso_timestamp(self):
        for bad in ["27/08/2026", "yesterday", "2026-13-45T00:00:00+00:00", ""]:
            with self.assertRaises(SchemaError, msg=bad):
                validate_timestamp(bad, "created")

    def test_accepts_an_offset_timestamp(self):
        self.assertEqual(
            validate_timestamp("2026-08-27T08:00:00+03:30", "created"),
            "2026-08-27T08:00:00+03:30",
        )

    def test_updated_may_not_precede_created(self):
        with self.assertRaises(SchemaError):
            meta(updated="2026-08-26T08:00:00+03:30")

    def test_touched_changes_only_updated(self):
        page = meta()
        later = page.touched("2026-08-28T09:00:00+03:30")
        self.assertEqual(later.created, page.created)
        self.assertEqual(later.updated, "2026-08-28T09:00:00+03:30")


class VocabularyTests(unittest.TestCase):
    def test_tags_are_normalized_and_sorted(self):
        self.assertEqual(normalize_tags(["Zeta", "alpha", "  Beta  ", "alpha"]),
                         ("alpha", "beta", "zeta"))

    def test_tags_must_be_a_list(self):
        with self.assertRaises(SchemaError):
            normalize_tags("one, two")

    def test_invalid_tag_characters_are_rejected(self):
        with self.assertRaises(SchemaError):
            normalize_tags(["has spaces and !!"])

    def test_sensitivity_is_validated(self):
        for value in SENSITIVITY_VALUES:
            self.assertEqual(meta(sensitivity=value).sensitivity, value)
        with self.assertRaises(SchemaError):
            meta(sensitivity="top-secret")

    def test_status_is_validated(self):
        with self.assertRaises(SchemaError):
            meta(status="whatever")

    def test_source_type_is_validated(self):
        with self.assertRaises(SchemaError):
            meta(source_type="telepathy")

    def test_summary_must_be_one_line(self):
        self.assertEqual(meta(summary="one\ntwo").summary, "one two")
        with self.assertRaises(SchemaError):
            meta(summary="   ")

    def test_link_fields_reject_traversal(self):
        for field in ("supersedes", "superseded_by"):
            with self.assertRaises(SchemaError, msg=field):
                meta(**{field: "../../etc/passwd"})
        with self.assertRaises(SchemaError):
            meta(related=["/absolute/path"])


class IdTests(unittest.TestCase):
    def test_ids_are_stable_for_the_same_page(self):
        first = generate_id("wiki_topic", relative_path="wiki/a.md", title="A", created=VALID["created"])
        second = generate_id("wiki_topic", relative_path="wiki/a.md", title="A", created=VALID["created"])
        self.assertEqual(first, second)

    def test_ids_differ_for_different_pages(self):
        a = generate_id("wiki_topic", relative_path="wiki/a.md", title="A", created=VALID["created"])
        b = generate_id("wiki_topic", relative_path="wiki/b.md", title="A", created=VALID["created"])
        self.assertNotEqual(a, b)

    def test_id_carries_a_type_prefix_and_the_creation_date(self):
        page_id = generate_id("output_plan", relative_path="outputs/a.md", title="A", created=VALID["created"])
        self.assertTrue(page_id.startswith("out-20260827-"))

    def test_invalid_ids_are_rejected(self):
        for bad in ["", "AB", "has space", "UPPER-20260827-abc", "x" * 100]:
            with self.assertRaises(SchemaError, msg=bad):
                meta(id=bad)


class RoundTripTests(unittest.TestCase):
    def test_a_rendered_page_parses_back_identically(self):
        original = meta(
            source='He said "hello", then left',
            related=["wiki/a", "wiki/b"],
            status="captured",
        )
        text = render_page(original, "Body with a [[wiki/link]].\n")
        page = parse_page(text, relative_path="raw/a.md")
        self.assertEqual(page.metadata, original)
        self.assertIn("[[wiki/link]]", page.body)

    def test_persian_content_round_trips(self):
        original = meta(title="یادداشت روزانه", summary="یک خلاصهٔ کوتاه")
        page = parse_page(render_page(original, "متن فارسی\n"), relative_path="raw/a.md")
        self.assertEqual(page.metadata.title, "یادداشت روزانه")
        self.assertIn("متن فارسی", page.body)

    def test_malformed_frontmatter_is_rejected_with_a_safe_message(self):
        with self.assertRaises(SchemaError) as ctx:
            parse_page("no frontmatter here", relative_path="raw/a.md")
        self.assertIn("could not be parsed", ctx.exception.message)

    def test_dangerous_yaml_in_a_page_is_rejected(self):
        text = "---\ntags: !!python/object/apply:os.system ['echo pwned']\n---\n\nbody\n"
        with self.assertRaises(SchemaError):
            parse_page(text, relative_path="raw/a.md")


if __name__ == "__main__":
    unittest.main()
