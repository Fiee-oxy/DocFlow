"""Focused-title ranking contracts; model scores are fixtures, not quality claims.

Run from services/retrieval in an environment with FastAPI and Pydantic:
    python -m unittest -v test_focus_ranking
No embedding, reranker model, Milvus, or chat LLM is loaded by these tests.
"""
import os
import unittest
from unittest.mock import patch

import app as retrieval


class FocusRankingTest(unittest.TestCase):
    def test_keyword_query_preserves_attributes_without_question_words(self):
        query = retrieval.keyword_query("How many tokens and sentences are in the test dataset?")
        self.assertIn("tokens",query);self.assertIn("sentences",query)
        self.assertNotIn("how",query.split())
        self.assertIn("学分",retrieval.keyword_query("毕业要求修满多少学分"))

    def test_lexical_routes_deduplicate_and_keep_route_unique_hits(self):
        merged=retrieval.merge_lexical_routes([[{"chunk_id":"a"},{"chunk_id":"b"}],[{"chunk_id":"b"},{"chunk_id":"c"}]],3)
        self.assertEqual(["b","a","c"],[x["chunk_id"] for x in merged])
    @staticmethod
    def candidate(chunk_id, leaf, content):
        section = "数据科学与大数据技术 / " + leaf
        return {
            "chunk_id": chunk_id,
            "content": content,
            "search_text": section + "\n" + content,
            "sources": [{"section": section}],
        }

    def rank(self, question, candidates, scores, *, focus_query="", title_boost=False):
        with patch.dict(os.environ, {"TITLE_MATCH_BONUS": "1.0", "FOCUS_TITLE_BONUS": "1.0"}), patch.object(
            retrieval, "rerank_scores", return_value=scores
        ) as model:
            result = retrieval.rerank(retrieval.RerankRequest(
                question=question,
                candidates=candidates,
                title_boost=title_boost,
                focus_query=focus_query,
            ))["candidates"]
        model.assert_called_once_with(question, [c["search_text"] for c in candidates])
        return result

    def test_question_noise_does_not_become_focus(self):
        terms = retrieval.focus_terms("大数据专业修满多少学分")
        self.assertIn("学分", terms)
        self.assertFalse(any("大数据" in term or "多少" in term or "修满" in term for term in terms))

    def test_minimum_credit_query_retains_attribute(self):
        self.assertIn("学分", retrieval.focus_terms("最低学分是多少"))

    def test_attribute_title_can_correct_small_model_score_gap(self):
        candidates = [
            self.candidate("semester", "分学期课程安排", "第一学期建议修读的课程及学分。"),
            self.candidate("graduation", "八、毕业最低学分", "修满 170 学分。此数字仅是测试夹具。"),
        ]
        result = self.rank(
            "大数据专业毕业要求修满多少学分", candidates, [6.8, 6.46],
            focus_query="大数据专业修满多少学分",
        )
        self.assertEqual("graduation", result[0]["chunk_id"])
        self.assertEqual(6.46, result[0]["model_rerank_score"])
        self.assertEqual(1.0, result[0]["focus_bonus"])
        self.assertEqual(0.0, result[1]["focus_bonus"])
        self.assertEqual(0.0, result[0]["title_bonus"])

    def test_absent_focus_query_does_not_apply_attribute_bonus(self):
        candidates = [
            self.candidate("semester", "分学期课程安排", "按学期安排课程。"),
            self.candidate("graduation", "毕业最低学分", "毕业需要达到学分要求。"),
        ]
        result = self.rank("大数据专业毕业要求修满多少学分", candidates, [6.8, 6.46])
        self.assertEqual("semester", result[0]["chunk_id"])
        self.assertTrue(all(c["focus_bonus"] == 0.0 for c in result))

    def test_overview_keeps_existing_full_leaf_title_bonus(self):
        candidates = [
            self.candidate("goals", "二、培养目标", "培养目标相关正文。"),
            self.candidate("requirements", "三、毕业要求", "毕业要求相关正文。"),
        ]
        result = self.rank("大数据专业毕业要求", candidates, [6.8, 6.46], title_boost=True)
        self.assertEqual("requirements", result[0]["chunk_id"])
        self.assertEqual(1.0, result[0]["title_bonus"])
        self.assertTrue(all(c["focus_bonus"] == 0.0 for c in result))

    def test_semester_and_recommended_qualifiers_are_preserved(self):
        focus = "大数据专业第一学期建议修读多少学分"
        terms = retrieval.focus_terms(focus)
        joined = " ".join(terms)
        self.assertIn("第一学期", joined)
        self.assertIn("建议修读", joined)
        self.assertIn("学分", joined)
        candidates = [
            self.candidate("graduation", "毕业最低学分", "毕业学分要求。"),
            self.candidate("semester", "第一学期建议修读学分", "第一学期建议修读课程。"),
        ]
        result = self.rank(focus, candidates, [6.8, 6.46], focus_query=focus)
        self.assertEqual("semester", result[0]["chunk_id"])
        graduation = next(c for c in result if c["chunk_id"] == "graduation")
        self.assertEqual(0.0, graduation["focus_bonus"])

    def test_course_category_qualifiers_are_not_dropped(self):
        for qualifier in ("选修", "必修"):
            with self.subTest(qualifier=qualifier):
                focus = "大数据专业" + qualifier + "学分是多少"
                self.assertIn(qualifier, " ".join(retrieval.focus_terms(focus)))
                candidates = [
                    self.candidate("graduation", "毕业最低学分", "总体学分要求。"),
                    self.candidate("category", qualifier + "学分", "对应课程类别的学分。"),
                ]
                result = self.rank(focus, candidates, [6.8, 6.46], focus_query=focus)
                self.assertEqual("category", result[0]["chunk_id"])
                graduation = next(c for c in result if c["chunk_id"] == "graduation")
                self.assertEqual(0.0, graduation["focus_bonus"])

    def test_partial_match_does_not_boost_unrelated_leaf(self):
        focus = "大数据专业第一学期选修学分是多少"
        candidates = [
            self.candidate("other-semester", "第二学期选修学分", "第二学期课程。"),
            self.candidate("required", "第一学期必修学分", "第一学期必修课程。"),
            self.candidate("match", "第一学期选修学分", "第一学期选修课程。"),
        ]
        result = self.rank(focus, candidates, [6.8, 6.7, 6.46], focus_query=focus)
        self.assertEqual("match", result[0]["chunk_id"])
        self.assertTrue(all(c["focus_bonus"] == 0.0 for c in result[1:]))


if __name__ == "__main__":
    unittest.main()
