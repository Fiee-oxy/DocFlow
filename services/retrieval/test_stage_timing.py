import unittest
from unittest.mock import Mock,patch
import app as r

class StageTimingTests(unittest.TestCase):
    def test_queue_timeout_is_separate_from_inference_and_context_is_reset(self):
        fake=Mock();fake.acquire.return_value=False
        with patch.object(r,"gate",fake),patch.object(r,"rerank_scores") as model:
            with self.assertRaises(r.HTTPException) as failure:r.rerank(r.RerankRequest(question="q",candidates=[dict(content="text")]))
        self.assertEqual("rerankQueue",failure.exception.detail["phase"])
        self.assertIn("rerankQueue",failure.exception.detail["timingMs"])
        model.assert_not_called();self.assertIsNone(r._score_timing.get())

    def test_timing_does_not_change_scores_or_argument_protocol(self):
        candidates=[dict(chunk_id="a",content="a"),dict(chunk_id="b",content="b")]
        with patch.object(r,"rerank_scores",return_value=[.1,.9]) as model:
            result=r.rerank(r.RerankRequest(question="q",candidates=candidates))
        model.assert_called_once_with("q",["a","b"])
        self.assertEqual(["b","a"],[c["chunk_id"] for c in result["candidates"]])
        self.assertGreaterEqual(result["timingMs"]["rerankServiceTotal"],result["timingMs"]["rerankQueue"])
        self.assertEqual(0,result["timingMs"]["rerankInference"]);self.assertIsNone(r._score_timing.get())
