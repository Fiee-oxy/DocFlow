"""Relevance order is not source order; unrelated snippets must not fabricate a fact."""
import importlib.util,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('coverage',Path(__file__).with_name('rag-coverage.py'));e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
class CoverageTest(unittest.TestCase):
    def test_fragment_order_does_not_lose_a_fact(self):
        case={'units':[{'pages':[1],'atoms':['103.1万亿元']}]}
        rows=[{'chunk_id':'second','content':'万亿元','sources':[{'pages':[1]}]}, {'chunk_id':'first','content':'103.1','sources':[{'pages':[1]}]}]
        self.assertTrue(e.coverage(rows,case,{'first':3,'second':4})['complete'])
        self.assertFalse(e.coverage(rows,case,{'first':3,'second':6})['complete'])
    def test_duplicate_routes_do_not_break_adjacency(self):
        case={'units':[{'pages':[],'atoms':['length of service and performance']}]}
        rows=[{'chunk_id':'a','content':'length of service'},{'chunk_id':'b','content':' and performance'}]
        self.assertTrue(e.coverage(rows+rows,case,{'a':1,'b':2})['complete'])
    def test_page_filter_cannot_join_across_an_excluded_source(self):
        case={'units':[{'pages':[1,3],'atoms':['103.1万亿元']}]}
        rows=[{'chunk_id':'a','content':'103.1','pages':[1]}, {'chunk_id':'b','content':'unrelated','pages':[2]}, {'chunk_id':'c','content':'万亿元','pages':[3]}]
        self.assertFalse(e.coverage(rows,case,{'a':1,'b':2,'c':3})['complete'])
if __name__=='__main__':unittest.main()
