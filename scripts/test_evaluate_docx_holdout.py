import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('holdout_eval',Path(__file__).with_name('evaluate-docx-holdout-retrieval.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)

class CoverageTests(unittest.TestCase):
    def test_markdown_emphasis_is_not_lost_evidence(self):
        q={'document':'doc','necessary':[{'source_type':'tbl','body_node':3,'anchors':['Fee42']}]}
        r.TABLE_ROWS[('doc',3)]=[['Fee','42']];r.TABLE_REFS[('doc',3)]='#/tables/0'
        self.assertTrue(r.coverage([{'content':'**Fee** = 42','sources':[{'ref':'b0:#/tables/0'}]}],q)['complete'])
        self.assertFalse(r.coverage([{'content':'Fee = 42','sources':[{'ref':'b0:#/tables/01'}]}],q)['complete'])
        self.assertFalse(r.coverage([{'content':'Fee = 43','sources':[{'ref':'b0:#/tables/0'}]}],q)['complete'])

    def test_native_provenance_and_multi_chunk_evidence(self):
        r.TABLE_ROWS[('doc',4)]=[['A','10','20']]
        q={'document':'doc','necessary':[{'source_type':'tbl','body_node':4,'anchors':['A1020']}]}
        hit={'content':'A, X = 10. Y = 20','sources':[{'fragments':[{'body_node':4}]}]}
        self.assertTrue(r.coverage([hit],q)['complete'])
        q={'necessary':[{'source_type':'p','anchors':['first','second']}]}
        self.assertTrue(r.coverage([{'content':'first'},{'content':'second'}],q)['complete'])
        self.assertFalse(r.coverage([{'content':'first'}],q)['complete'])

if __name__=='__main__':unittest.main()
