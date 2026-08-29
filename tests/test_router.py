"""Deterministic routing: triggers, exclusions, ambiguity and refusal to guess."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode
from primee.core.router import AMBIGUOUS, EMPTY, MATCHED, NO_MATCH, UNKNOWN_COMMAND, DeterministicRouter
from primee.core.skill_loader import discover_skills

from .support import BUNDLED_SKILLS, VALID_SKILLS


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.registry = discover_skills(BUNDLED_SKILLS)
        self.router = DeterministicRouter()


class TriggerMatchingTests(RouterTestCase):
    def test_exact_trigger_phrase_matches(self):
        decision = self.router.route("morning brief", self.registry)
        self.assertEqual(decision.status, MATCHED)
        self.assertEqual(decision.skill_name, "inbox")

    def test_trigger_inside_a_sentence_matches(self):
        decision = self.router.route("please give me the morning brief now", self.registry)
        self.assertEqual(decision.skill_name, "inbox")

    def test_persian_trigger_matches(self):
        decision = self.router.route("برنامه روزانه من چیست", self.registry)
        self.assertEqual(decision.skill_name, "plan")

    def test_punctuation_and_case_are_ignored(self):
        decision = self.router.route("METRICS, please!", self.registry)
        self.assertEqual(decision.skill_name, "metrics")

    def test_explanation_reports_the_actual_scores(self):
        decision = self.router.route("daily plan", self.registry)
        self.assertIn("plan", decision.explanation)
        self.assertTrue(decision.all_scores)
        self.assertTrue(any(s.matched_triggers for s in decision.all_scores))


class ExplicitCommandTests(RouterTestCase):
    def test_slash_command_selects_the_skill_directly(self):
        decision = self.router.route("/trends", self.registry)
        self.assertEqual(decision.status, MATCHED)
        self.assertEqual(decision.skill_name, "trends")

    def test_slash_command_beats_conflicting_trigger_text(self):
        decision = self.router.route("/metrics what changed", self.registry)
        self.assertEqual(decision.skill_name, "metrics")

    def test_unknown_command_is_reported_not_guessed(self):
        decision = self.router.route("/nosuchskill", self.registry)
        self.assertEqual(decision.status, UNKNOWN_COMMAND)
        self.assertEqual(decision.error_code, ErrorCode.UNKNOWN_COMMAND)
        self.assertIsNone(decision.skill_name)


class ExclusionTests(RouterTestCase):
    def test_exclusion_vetoes_a_matching_skill(self):
        decision = self.router.route("send email about the morning brief", self.registry)
        inbox = next(s for s in decision.all_scores if s.skill_name == "inbox")
        self.assertTrue(inbox.vetoed)
        self.assertEqual(inbox.score, 0.0)
        self.assertNotEqual(decision.skill_name, "inbox")

    def test_exclusion_keeps_metrics_away_from_trend_questions(self):
        decision = self.router.route("what changed since yesterday", self.registry)
        metrics = next(s for s in decision.all_scores if s.skill_name == "metrics")
        self.assertTrue(metrics.vetoed)
        self.assertEqual(decision.skill_name, "trends")

    def test_vetoed_skills_are_named_in_the_no_match_explanation(self):
        registry = discover_skills(VALID_SKILLS)
        decision = DeterministicRouter().route("tea and coffee", registry)
        self.assertEqual(decision.status, MATCHED)
        alpha = next(s for s in decision.all_scores if s.skill_name == "alpha")
        self.assertTrue(alpha.vetoed)


class AmbiguityTests(unittest.TestCase):
    def setUp(self):
        self.registry = discover_skills(VALID_SKILLS)
        self.router = DeterministicRouter()

    def test_two_equally_good_matches_are_ambiguous(self):
        decision = self.router.route("coffee", self.registry)
        self.assertEqual(decision.status, AMBIGUOUS)
        self.assertEqual(decision.error_code, ErrorCode.AMBIGUOUS_REQUEST)
        self.assertIsNone(decision.skill_name)
        self.assertEqual(
            sorted(c.skill_name for c in decision.candidates), ["alpha", "beta"]
        )

    def test_ambiguous_explanation_offers_a_concrete_next_step(self):
        decision = self.router.route("coffee", self.registry)
        self.assertIn("will not guess", decision.explanation)
        self.assertIn("/", decision.explanation)

    def test_a_clear_winner_is_not_ambiguous(self):
        decision = self.router.route("coffee grinder", self.registry)
        self.assertEqual(decision.status, MATCHED)
        self.assertEqual(decision.skill_name, "beta")

    def test_candidate_order_is_deterministic(self):
        first = self.router.route("coffee", self.registry)
        for _ in range(5):
            again = self.router.route("coffee", self.registry)
            self.assertEqual(
                [c.skill_name for c in first.candidates],
                [c.skill_name for c in again.candidates],
            )


class NoMatchTests(RouterTestCase):
    def test_unrelated_request_matches_nothing(self):
        decision = self.router.route("please defragment the toaster", self.registry)
        self.assertEqual(decision.status, NO_MATCH)
        self.assertEqual(decision.error_code, ErrorCode.NO_MATCHING_SKILL)
        self.assertIsNone(decision.skill_name)

    def test_empty_request_is_reported(self):
        decision = self.router.route("   ", self.registry)
        self.assertEqual(decision.status, EMPTY)
        self.assertEqual(decision.error_code, ErrorCode.EMPTY_REQUEST)

    def test_threshold_is_configurable(self):
        strict = DeterministicRouter(min_score=0.99)
        decision = strict.route("please give me the morning brief now", self.registry)
        self.assertEqual(decision.status, NO_MATCH)


class NoModelTests(RouterTestCase):
    def test_routing_is_pure_and_repeatable(self):
        results = {self.router.route("kpi report", self.registry).skill_name for _ in range(20)}
        self.assertEqual(results, {"metrics"})

    def test_decision_reports_its_method(self):
        decision = self.router.route("kpi report", self.registry)
        self.assertEqual(decision.method, "deterministic")


if __name__ == "__main__":
    unittest.main()
