import unittest
from types import SimpleNamespace as NS
from normalization import normalize, annotate_batch


def item(text, label="text", page=1, level=None):
    value = NS(text=text, label=NS(value=label), prov=[NS(page_no=page)] if page else [], self_ref="#/texts/1")
    if level:
        value.level = level
    return value


class NormalizationTests(unittest.TestCase):
    def test_flat_programme_headings_keep_parent_and_distinct_chapters(self):
        batch={"blocks":[{"text":t,"label":l,"ref":str(i)} for i,(t,l) in enumerate([
            ("数据科学与大数据技术专业培养方案（2023年版）","section_header"),
            ("二、培养目标","section_header"),("毕业后五年目标","text"),
            ("三、毕业要求","section_header"),("工程知识与问题分析","text")])]}
        state=annotate_batch(batch)
        self.assertIn("专业培养方案",batch["blocks"][2]["section"])
        self.assertTrue(batch["blocks"][2]["section"].endswith("二、培养目标"))
        self.assertTrue(batch["blocks"][4]["section"].endswith("三、毕业要求"))
        next_batch={"blocks":[{"text":"后续毕业要求","label":"text"}]}
        annotate_batch(next_batch,state)
        self.assertEqual(next_batch["blocks"][0]["section"],batch["blocks"][4]["section"])
    def document(self, items, pages=(1,)):
        return NS(iterate_items=lambda: iter((value, 0) for value in items), pages=dict.fromkeys(pages))

    def test_reading_order_nested_headings_and_physical_pages(self):
        result = normalize(self.document([
            item("第一章", "section_header"), item("条件", "section_header", level=2),
            item("条件内容", page=2), item("第二章", "section_header", page=3), item("末尾独有知识", page=3),
        ], (1, 2, 3)))
        self.assertEqual(result["blocks"][2]["section"], "第一章 / 条件")
        self.assertEqual(result["blocks"][-1]["text"], "末尾独有知识")
        self.assertEqual(result["blocks"][-1]["section"], "第二章")
        self.assertEqual(result["blocks"][-1]["pages"], [3])

    def test_tables_use_structured_export_and_empty_pages_warn(self):
        table = item("", "table")
        table.export_to_markdown = lambda doc: "|字段|含义|\n|---|---|\n|id|标识|"
        result = normalize(self.document([item("页眉", "page_header"), table], (1, 2)))
        self.assertEqual(len(result["blocks"]), 1)
        self.assertIn("|id|", result["blocks"][0]["text"])
        self.assertIn("2", result["warnings"][0])

    def test_docx_has_paragraph_location_without_invented_page_number(self):
        result = normalize(self.document([item("原文", page=None)], ()))
        self.assertEqual(result["blocks"][0]["pages"], [])
        self.assertEqual(result["blocks"][0]["ref"], "#/texts/1")


if __name__ == "__main__":
    unittest.main()
