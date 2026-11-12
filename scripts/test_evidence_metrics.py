import unittest,itertools
from evidence_metrics import coverage
class EvidenceMetricsTest(unittest.TestCase):
    def setUp(self):
        self.items=[{'id':'a','text':'hello ','pages':[1]},{'id':'b','text':'world','pages':[1]},
                    {'id':'c','text':' extra','pages':[1]}]
        self.order={'a':0,'b':1,'c':2}
        self.case={'units':[{'pages':[1],'atoms':['hello world']}]}
    def test_rank_order_does_not_change_match(self):
        for permutation in itertools.permutations(self.items):self.assertTrue(coverage(permutation,self.case,self.order)['complete'])
    def test_does_not_bridge_missing_text(self):
        case={'units':[{'pages':[1],'atoms':['hello extra']}]}
        self.assertFalse(coverage([self.items[0],self.items[2]],case,self.order)['complete'])
    def test_subset_cannot_gain_evidence_over_superset(self):
        for size in range(4):
            for subset in itertools.combinations(self.items,size):
                if coverage(subset,self.case,self.order)['complete']:
                    self.assertTrue(coverage(self.items,self.case,self.order)['complete'])
    def test_wrong_page_cannot_match(self):
        self.case['units'][0]['pages']=[2]
        self.assertFalse(coverage(self.items,self.case,self.order)['complete'])
    def test_unknown_source_is_error(self):
        with self.assertRaises(ValueError):coverage(self.items,self.case,{})
if __name__=='__main__':unittest.main()
