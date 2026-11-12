import unittest
from retrieval_router import plan,analyze,assess,fuse,RuleDictionary,DEFAULT_PATH,FallbackPolicy


class RouterTests(unittest.TestCase):
    def test_spec_examples_and_conflicts(self):
        examples=[("查找包含‘为什么’的句子","BM25"),("为什么 Redis 会出现缓存击穿","HYBRID"),
            ("找出与这段话意思相近的内容","DENSE"),("对比两个文档中出现的错误码","HYBRID"),
            ("第三章讲了什么","HYBRID"),("总结全文","HYBRID"),("What does HTTP 403 mean?","HYBRID"),
            ('find exact "Redis" and compare documents',"HYBRID"),('Find "why all documents differ"',"BM25"),
            ('do not locate "code"',"HYBRID"),("Find semantically similar text containing 0x154","HYBRID")]
        for query,expected in examples:
            with self.subTest(query=query):self.assertEqual(expected,plan(query)["mode"])

    def test_identifier_patterns_require_outer_literal_intent(self):
        for identifier in ("550e8400-e29b-41d4-a716-446655440000","0x154","HTTP 403","file.docx","/api/users/{id}","C:\\docs\\a.pdf","UserService.getUser()","RFC 9110"):
            with self.subTest(identifier=identifier):
                self.assertTrue(analyze(identifier).hasExactIdentifier)
                self.assertEqual("BM25",plan("find exact "+identifier)["mode"])
                self.assertEqual("HYBRID",plan("What is "+identifier)["mode"])
        self.assertEqual("HYBRID",plan("Redis MySQL Spring Boot")['mode'])

    def test_scope_is_not_mode_and_grounding_prevents_none(self):
        self.assertEqual("NONE",plan("你好",dict(documentGrounded=False,generalChatMode=True,documentIds=[]))["mode"])
        self.assertEqual("HYBRID",plan("你好",dict(documentGrounded=True,generalChatMode=True,documentIds=["d"]))["mode"])
        ctx=dict(scope=dict(sectionIds=["s"]),attributes=dict(resolvedSectionIds=["s"]))
        self.assertEqual("HYBRID",plan("第三章 Redis 缓存原因",ctx)["mode"])
        self.assertEqual("SECTION_READ",plan("读取第三章原文",ctx)["mode"])
        self.assertEqual("STRUCTURED",plan("文件大小",dict(attributes=dict(metadataQuerySupported=True)))["mode"])

    def test_switches_unicode_and_version(self):
        self.assertEqual("HYBRID",plan('find exact "Redis"',enabled_rules={"exact-lookup":False})["mode"])
        self.assertEqual("BM25",plan("ｆｉｎｄ exact 0x154")["mode"])
        self.assertEqual("retrieval-router-v2",plan("query")["ruleVersion"])
        self.assertEqual(64,len(RuleDictionary(DEFAULT_PATH).sha))
        self.assertEqual("HYBRID",plan('Explain "'+('find exact 0x154 '*30)+'"')["mode"])

    def test_gate_checks_nonempty_quality_and_never_skips_model(self):
        self.assertFalse(assess("query",[],"BM25")["accepted"])
        self.assertFalse(assess("query",[dict(score=999,content='unrelated')],"BM25")["accepted"])
        self.assertFalse(assess('find "literal"',[dict(content="literal")],"BM25")["skipRerank"])
        self.assertEqual('DENSE',FallbackPolicy().next_channel("BM25",dict(reason="LOW_CONFIDENCE",accepted=False)))
        self.assertIsNone(FallbackPolicy().next_channel("BM25",dict(reason="EMPTY_RESULT"),attempts=1))
        self.assertEqual("DENSE",FallbackPolicy().next_channel("BM25",dict(reason="EMPTY_RESULT")))

    def test_exact_constraint_is_required_in_one_candidate_not_scattered_across_hits(self):
        quality=assess('find exact "ALPHA-123" "BETA-456"',[dict(content='ALPHA-123'),dict(content='BETA-456')],'BM25')
        self.assertEqual('LOW_CONFIDENCE',quality['reason']);self.assertIn('EXACT_CONSTRAINT_MISSING',quality['reasons'])
        self.assertTrue(assess('find exact 0x154',[dict(content='ERROR 0x154 occurred')],'BM25')['accepted'])
        self.assertFalse(assess('find exact 0x154',[dict(content='0x1540')],'BM25')['accepted'])

    def test_coverage_is_bounded_body_based_and_language_aware(self):
        quality=assess('find exact revenue profit assets liabilities',[dict(score=1e6,content='revenue',search_text='revenue profit assets liabilities')],'BM25')
        self.assertEqual(.25,quality['lexicalCoverage']);self.assertFalse(quality['accepted'])
        self.assertTrue(assess('查找毕业学分',[dict(content='毕业学分要求为160学分')],'BM25')['accepted'])
        self.assertTrue(assess('Find "ＡＬＰＨＡ"',[dict(content='alpha')],'BM25')['accepted'])

    def test_dense_threshold_is_cosine_not_bm25_or_a_score_margin(self):
        self.assertTrue(assess('semantically similar car',[dict(content='automobile',score=.7),dict(content='vehicle',score=.7)],'DENSE')['accepted'])
        low=assess('semantically similar car',[dict(content='car',score=.2)],'DENSE')
        self.assertIn('DENSE_SIMILARITY_LOW',low['reasons'])
        self.assertFalse(assess('q',[dict(score=float('nan'))],'DENSE')['accepted'])
        self.assertTrue(assess('q',[dict(score=.45)],'DENSE')['accepted'])
        self.assertTrue(assess('q',[dict(score=.2)],'DENSE',dense_min_score=.1)['accepted'])

    def test_switches_no_recursive_fallback_and_invalid_thresholds(self):
        low=dict(reason='LOW_CONFIDENCE',accepted=False)
        self.assertIsNone(FallbackPolicy(on_low_confidence=False).next_channel('DENSE',low))
        self.assertEqual('BM25',FallbackPolicy(on_empty=False).next_channel('DENSE',low))
        self.assertIsNone(FallbackPolicy().next_channel('HYBRID',low))
        self.assertIsNone(FallbackPolicy().next_channel('DENSE',low,attempts=1))
        for kwargs in [dict(dense_min_score=float('nan')),dict(bm25_min_coverage=2),dict(quality_top_k=0)]:
            with self.assertRaises(ValueError):assess('q',[],'DENSE',**kwargs)

    def test_fusion_keeps_ranks_and_single_id(self):
        a=dict(chunk_id="a");b=dict(chunk_id="b");result=fuse([a,a],[a,b])
        self.assertEqual(2,len(result));self.assertEqual(1,result[0]["dense_rank"]);self.assertEqual(1,result[0]["bm25_rank"])


if __name__=="__main__":unittest.main()
