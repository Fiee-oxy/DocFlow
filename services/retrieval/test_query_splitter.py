import unittest
from query_splitter import split_query

class SplitterTests(unittest.TestCase):
    def test_explicit_english_preserves_identifiers_and_years(self):
        plan=split_query('In 2024 what was Acme revenue, and how many employees did it have?')
        self.assertEqual(len(plan['queries']),2)
        self.assertTrue(all('2024' in q and 'Acme' in q for q in plan['queries']))
    def test_company_and_and_noun_list_are_not_question_boundaries(self):
        for q in ('What is Johnson and Johnson revenue?', 'What are revenue and profit in 2024?',
                  'Find exact "revenue and how it changed"', 'How many research and development staff were employed?'):
            self.assertEqual(split_query(q)['queries'],[])
    def test_explicit_chinese_and_shared_subject(self):
        plan=split_query('某公司2024年收入是多少，员工数量有多少？')
        self.assertEqual(len(plan['queries']),2)
        self.assertTrue(all('2024年' in q for q in plan['queries']))
        self.assertIn('某公司',plan['queries'][1])
    def test_no_new_facts_and_bounded_ambiguity(self):
        for q in ('', 'x'*1501, 'What is A and how is B and why is C?', '请解释毕业要求'):
            self.assertEqual(split_query(q)['queries'],[])

if __name__=='__main__':unittest.main()
