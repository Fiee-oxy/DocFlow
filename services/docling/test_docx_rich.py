import hashlib
import io
import json
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from docx_stream import W, R, M
from docx_rich import A, V, MC
import test_docx_stream as fixtures
from test_docx_stream import paragraph, table, row, cell


class RichDocxTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DocxStreamTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.package, self.parse = self.fixture.package, self.fixture.parse
        self.blocks, self.batches = self.fixture.blocks, self.fixture.batches
        image = Image.new("RGB", (400, 100), "white")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        self.image = stream.getvalue()

    def picture(self, ident="image", alt="Chart description"):
        return (f'<w:r><w:drawing><a:graphic xmlns:a="{A}"><a:docPr descr="{alt}"/>'
                f'<a:blip r:embed="{ident}"/></a:graphic></w:drawing></w:r>')

    def image_package(self, body, **kwargs):
        return self.package(body, relationships=f'<Relationship Id="image" Type="{R}/image" Target="media/test.png"/>',
                            extra={"word/media/test.png": self.image}, **kwargs)

    def test_image_resource_alt_and_hash_are_retained_without_fabricated_ocr(self):
        self.image_package('<w:p>' + self.picture() + '</w:p>')
        events = self.parse()
        block = self.blocks(events)[0]
        asset = block["images"][0]
        self.assertEqual(asset["sha256"], hashlib.sha256(self.image).hexdigest())
        self.assertEqual((asset["width"], asset["height"]), (400,100))
        self.assertEqual(asset["status"], "RESOURCE_ONLY")
        self.assertEqual(asset["part"], "word/media/test.png")
        self.assertIn("Chart description", block["text"])
        self.assertFalse(events[-1]["partial"])
        self.assertTrue(any("OCR_DISABLED" in w for w in events[-1]["warnings"]))

    def test_ocr_word_confidence_bbox_cache_and_stable_resume(self):
        self.image_package('<w:p>' + self.picture() + '</w:p>' + '<w:p>' + self.picture() + '</w:p>')
        tsv = b'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t60\t30\t97.5\tINVOICE\n'
        with patch("docx_rich.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=tsv, stderr=b"")) as run:
            events = self.parse(ocr_enabled=True, docx_batch_nodes=1)
            self.assertEqual(run.call_count, 1)
            blocks = self.blocks(events)
            self.assertIn("INVOICE", blocks[0]["text"])
            self.assertEqual(blocks[0]["images"][0]["words"][0]["bbox"], [10,20,60,30])
            self.assertEqual(blocks[0]["images"][0]["words"][0]["confidence"],97.5)
            self.assertNotEqual(blocks[0]["images"][0]["id"], blocks[1]["images"][0]["id"])
            resumed=self.parse(ocr_enabled=True,docx_batch_nodes=1,resume_state=json.loads(json.dumps(self.batches(events)[0]["resumeState"])))
            self.assertEqual(self.batches(resumed), self.batches(events)[1:])

    def test_ocr_failure_is_not_reported_as_success(self):
        self.image_package('<w:p>' + self.picture() + '</w:p>')
        with patch("docx_rich.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError,"OCR_UNAVAILABLE"):
                self.parse(ocr_enabled=True)
        with self.assertRaisesRegex(ValueError,"IMAGE_LIMIT"):
            self.parse(docx_max_image_pixels=100)

    @unittest.skipUnless(shutil.which("tesseract"), "real Tesseract is available in the parser container")
    def test_real_ocr_reads_raster_image_text(self):
        image=Image.new('RGB',(1100,180),'white')
        ImageDraw.Draw(image).text((30,35),'INVOICE TOTAL 12345',font=ImageFont.load_default(size=56),fill='black')
        stream=io.BytesIO();image.save(stream,format='PNG');self.image=stream.getvalue()
        self.image_package('<w:p>'+self.picture(alt='')+'</w:p>')
        events=self.parse(ocr_enabled=True,ocr_languages=['eng'])
        asset=self.blocks(events)[0]['images'][0]
        self.assertEqual(asset['status'],'OCR_COMPLETE')
        self.assertIn('INVOICE',asset['text'])
        self.assertIn('12345',asset['text'])
        self.assertTrue(asset['words'])

    def test_external_images_are_never_downloaded(self):
        self.package('<w:p>'+self.picture()+'</w:p>',relationships=f'<Relationship Id="image" Type="{R}/image" Target="https://example.invalid/image.png" TargetMode="External"/>')
        with self.assertRaisesRegex(ValueError,"EXTERNAL_IMAGE"):
            self.parse()

    def test_content_controls_keep_inner_paragraphs_and_tables(self):
        self.package('<w:sdt><w:sdtPr/><w:sdtContent>'+paragraph("first")+table([row([cell("A"),cell("B")])],columns=2)+paragraph("last")+'</w:sdtContent></w:sdt>')
        blocks=self.blocks(self.parse())
        self.assertEqual([b['label'] for b in blocks],["text","table","text"])
        self.assertEqual(len({b['node_ref'] for b in blocks}),3)
        self.assertEqual({b['body_node'] for b in blocks},{0})

    def test_image_in_control_or_table_is_published_once_without_changing_native_text(self):
        body='<w:sdt><w:sdtContent><w:p><w:r><w:t>before</w:t></w:r>'+self.picture()+'<w:r><w:t>after</w:t></w:r></w:p></w:sdtContent></w:sdt>'
        self.image_package(body)
        blocks=self.blocks(self.parse())
        self.assertEqual([b['text'] for b in blocks],['beforeafter','Chart description'])
        self.assertEqual(sum(b.get('content_kind')=='image_resource' for b in blocks),1)
        self.image_package(table([row(['<w:tc><w:p><w:r><w:t>caption</w:t></w:r>'+self.picture()+'</w:p></w:tc>',cell('value')])],columns=2))
        blocks=self.blocks(self.parse())
        self.assertEqual(blocks[0]['table_data']['table_cells'][0]['text'],'caption')
        self.assertEqual(blocks[1]['content_kind'],'image_resource')
        self.assertIn('/row:0/cell:0/',blocks[1]['images'][0]['anchor'])

    def test_headers_footers_fields_and_part_relationships(self):
        body=paragraph("body")+'<w:sectPr><w:headerReference r:id="hdr"/><w:footerReference r:id="ftr"/></w:sectPr>'
        header=f'<w:hdr xmlns:w="{W}" xmlns:r="{R}"><w:p><w:hyperlink r:id="link"><w:r><w:t>Company</w:t></w:r></w:hyperlink></w:p></w:hdr>'
        footer=f'<w:ftr xmlns:w="{W}"><w:p><w:fldSimple w:instr="PAGE"><w:r><w:t>7</w:t></w:r></w:fldSimple></w:p></w:ftr>'
        self.package(body,relationships=f'<Relationship Id="hdr" Type="{R}/header" Target="header1.xml"/><Relationship Id="ftr" Type="{R}/footer" Target="footer1.xml"/>',extra={
            'word/header1.xml':header,'word/footer1.xml':footer,
            'word/_rels/header1.xml.rels':f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="link" Type="{R}/hyperlink" Target="https://example.invalid" TargetMode="External"/></Relationships>'})
        events=self.parse(docx_batch_nodes=1)
        blocks=self.blocks(events)
        self.assertEqual([b['text'] for b in blocks],["body","Company","7"])
        self.assertEqual([b.get('part_kind') for b in blocks],[None,'header','footer'])
        self.assertEqual(blocks[-1]['fields'][0]['evaluation'],'SAVED_RESULT_ONLY')
        state=self.batches(events)[-1]['resumeState']
        self.assertEqual(self.blocks(self.parse(docx_batch_nodes=1,resume_state=state)),[])

    def test_field_cross_paragraph_checkpoint_uses_only_saved_result(self):
        self.package('<w:p><w:r><w:fldChar w:fldCharType="begin"/><w:instrText>DO_NOT_EXECUTE</w:instrText></w:r></w:p>'
                     '<w:p><w:r><w:fldChar w:fldCharType="separate"/><w:t>cached result</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>')
        full=self.parse(docx_batch_nodes=1)
        self.assertEqual([b['text'] for b in self.blocks(full)],["cached result"])
        self.assertEqual(self.batches(self.parse(docx_batch_nodes=1,resume_state=self.batches(full)[0]['resumeState'])),self.batches(full)[1:])
        self.package('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r></w:p>')
        with self.assertRaisesRegex(ValueError,'unclosed field'):
            self.parse()

    def test_revisions_final_view_and_formula_preserve_source(self):
        body='<w:p><w:del><w:r><w:delText>obsolete</w:delText></w:r></w:del><w:ins><w:r><w:t>current </w:t></w:r></w:ins><m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath></w:p>'
        self.package(body)
        block=self.blocks(self.parse())[0]
        self.assertEqual(block['text'],'current (a)/(b)')
        self.assertIn('oMath',block['formulas'][0]['ooxml'])
        self.assertEqual(block['revision_view'],'FINAL_SAVED_VIEW')

    def test_nested_tables_keep_structured_cells(self):
        nested=table([row([cell('nested key'),cell('nested value')])],columns=2)
        self.package(table([row(['<w:tc>'+paragraph('outer')+nested+'</w:tc>',cell('other')])],columns=2))
        block=self.blocks(self.parse())[0]
        self.assertEqual(block['table_data']['num_cols'],2)
        self.assertIn('nested value',block['table_data']['table_cells'][0]['text'])
        self.assertEqual(block['nested_tables'][0]['table_data']['table_cells'][1]['text'],'nested value')

    def test_content_controls_can_wrap_rows_and_cells(self):
        source='<w:tbl><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid><w:sdt><w:sdtContent><w:tr><w:sdt><w:sdtContent>'+cell('first')+'</w:sdtContent></w:sdt>'+cell('second')+'</w:tr></w:sdtContent></w:sdt></w:tbl>'
        self.package(source)
        data=self.blocks(self.parse())[0]['table_data']
        self.assertEqual((data['num_rows'],data['num_cols']),(1,2))
        self.assertEqual([c['text'] for c in data['table_cells']],['first','second'])

    def test_multilevel_parent_start_override_is_used(self):
        numbering='<w:abstractNum w:abstractNumId="1">'+''.join(f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>' for i in range(3))+'</w:abstractNum><w:num w:numId="7"><w:abstractNumId w:val="1"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="15"/></w:lvlOverride><w:lvlOverride w:ilvl="1"><w:startOverride w:val="12"/></w:lvlOverride></w:num>'
        self.package(paragraph('item','<w:pPr><w:numPr><w:numId w:val="7"/><w:ilvl w:val="2"/></w:numPr></w:pPr>'),numbering=numbering)
        self.assertEqual(self.blocks(self.parse())[0]['marker'],'15.12.1')

    def test_footnotes_are_preserved_once_and_resume_deduplicates(self):
        ref='<w:p><w:r><w:t>text</w:t><w:footnoteReference w:id="2"/></w:r></w:p>'
        self.package(ref+ref,extra={'word/footnotes.xml':f'<w:footnotes xmlns:w="{W}"><w:footnote w:id="2">{paragraph("note content")}</w:footnote></w:footnotes>'})
        full=self.parse(docx_batch_nodes=1)
        self.assertEqual(sum('note content' in b['text'] for b in self.blocks(full)),1)
        self.assertEqual(self.batches(self.parse(docx_batch_nodes=1,resume_state=self.batches(full)[0]['resumeState'])),self.batches(full)[1:])


if __name__=='__main__':
    unittest.main()
