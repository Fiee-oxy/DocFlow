import json
import unittest
from copy import deepcopy
import test_docx_stream as fixtures
from docx_batch_boundary import table_decision, caption


def table(node, title="Table 1", section="Chapter A", value="alpha"):
    cells=[]
    for row, texts in enumerate((("Item", "Value"), (value, "42"))):
        for col,text in enumerate(texts):
            cells.append(dict(text=text,start_row_offset_idx=row,end_row_offset_idx=row+1,
                start_col_offset_idx=col,end_col_offset_idx=col+1,column_header=row==0))
    return dict(label="table",text=value,section=section,body_node=node,table_id=str(node),
        table_caption=title,table_data=dict(num_rows=2,num_cols=2,table_cells=cells))


class BoundaryTests(unittest.TestCase):
    def test_explicit_continuation_preserves_originals_and_offsets(self):
        result=table_decision(table(0),table(1,"Table 1 (continued)",value="beta"))
        self.assertTrue(result["merge"])
        b=result["block"]
        self.assertEqual(b["table_data"]["num_rows"],3)
        self.assertEqual(len(b["source_fragments"]),2)
        self.assertEqual(b["table_data"]["table_cells"][-1]["start_row_offset_idx"],2)
        self.assertIn("beta",b["text"])

    def test_reject_ambiguous_tables(self):
        for change in ({"table_caption":"Table 1"},{"section":"Chapter B"},
                       {"body_node":3},{"images":[{}]},{"section":""}):
            b=table(1,"Table 1 (continued)");b.update(change)
            self.assertFalse(table_decision(table(0),b)["merge"])

    def test_reject_changed_or_missing_header(self):
        for key,value in (("text","Changed"),("column_header",False)):
            b=table(1,"Table 1 (continued)")
            b["table_data"]["table_cells"][0][key]=value
            self.assertFalse(table_decision(table(0),b)["merge"])

    def test_intervening_caption_must_have_same_continuation_identity(self):
        a,b=table(0),table(2,'Table 1 (continued)')
        for text in ('Table 99 (continued)','Table 1','unrelated prose'):
            self.assertFalse(table_decision(a,b,{'body_node':1,'text':text})['merge'])
        self.assertTrue(table_decision(a,b,{'body_node':1,'text':'Table 1 (continued)'})['merge'])

    def test_chinese_caption(self):
        self.assertEqual(caption("表 2（续）"),{"id":"2","continued":True})

    def test_native_table_merge_only_before_publication(self):
        fixture=fixtures.DocxStreamTests();fixture.setUp()
        try:
            def native(title,value):
                return '<w:tbl><w:tblPr><w:tblCaption w:val="'+title+'"/></w:tblPr><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>'+fixtures.row([fixtures.cell('Item'),fixtures.cell('Value')],'<w:trPr><w:tblHeader/></w:trPr>')+fixtures.row([fixtures.cell(value),fixtures.cell('42')])+'</w:tbl>'
            body=fixtures.paragraph('Chapter','<w:pPr><w:outlineLvl w:val="0"/></w:pPr>')+native('Table 1','alpha')+native('Table 1 (continued)','beta')
            fixture.package(body)
            events=fixture.parse(docx_boundary_aware=True,docx_batch_nodes=2)
            blocks=fixture.blocks(events)
            tables=[b for b in blocks if b['label']=='table']
            self.assertEqual(len(tables),1)
            self.assertEqual(tables[0]['table_data']['num_rows'],3)
            self.assertEqual([f['body_node'] for f in tables[0]['source_fragments']],[1,2])
            self.assertEqual(fixture.batches(events)[0]['docxBoundary']['tableMerges'],1)
            self.assertEqual(len([b for b in fixture.blocks(fixture.parse(docx_batch_nodes=2)) if b['label']=='table']),2)
        finally:fixture.tearDown()

    def test_boundary_packing_and_every_resume_cursor(self):
        fixture=fixtures.DocxStreamTests();fixture.setUp()
        try:
            body=fixtures.paragraph("Chapter",'<w:pPr><w:outlineLvl w:val="0"/></w:pPr>')
            body+=''.join(fixtures.paragraph("short "+str(i)) for i in range(14))
            fixture.package(body)
            settings=dict(docx_batch_nodes=2,docx_batch_chars=12000,docx_boundary_aware=True,
                          docx_boundary_lookahead_nodes=3)
            events=fixture.parse(**settings)
            batches=fixture.batches(events)
            self.assertGreater(len(batches),1)
            self.assertLessEqual(batches[0]["endNode"],5)
            self.assertEqual(len(fixture.blocks(events)),15)
            self.assertEqual(fixture.blocks(events)[1]["text"],"short 0")
            for index,batch in enumerate(batches):
                resumed=fixture.parse(**settings,resume_state=json.loads(json.dumps(batch["resumeState"])))
                self.assertEqual(fixture.batches(resumed),batches[index+1:])
                self.assertEqual(resumed[-1],events[-1])
            self.assertRaises(ValueError,fixture.parse,**(settings|{"docx_boundary_aware":False}),
                              resume_state=batches[0]["resumeState"])
            self.assertRaises(ValueError,fixture.parse,**(settings|{"docx_boundary_lookahead_nodes":4}),
                              resume_state=batches[0]["resumeState"])
            self.assertEqual(fixture.parse(docx_batch_nodes=2),fixture.parse(docx_batch_nodes=2,docx_boundary_aware=False))
        finally:
            fixture.tearDown()

    def test_table_limits_and_column_change_reject(self):
        a,b=table(0),table(1,'Table 1 (continued)')
        self.assertFalse(table_decision(a,b,max_cells=2)['merge'])
        self.assertFalse(table_decision(a,b,max_chars=2)['merge'])
        b['table_data']['num_cols']=3
        self.assertFalse(table_decision(a,b)['merge'])


if __name__=="__main__":
    unittest.main()
