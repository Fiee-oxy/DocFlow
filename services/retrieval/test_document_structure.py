import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import app
from document_structure import ast_for
from test_continuity_chunking import tiny_tokenizer


class StructureTests(unittest.TestCase):
    def request(self,blocks,**settings):
        return app.ChunkRequest(document_id='doc',owner_id=7,index_version='v1',batch_index=0,
            batch={'blocks':blocks},hierarchy_enabled=True,target_tokens=40,max_tokens=64,min_preferred_tokens=1,**settings)

    def blocks(self):
        return [{'ref':f'node:{i}','text':' '.join(['word']*25)+f' value{i}',
            'section':'Chapter / Section','pages':[i+1],'label':'text'} for i in range(4)]

    def test_stable_ids_raw_sources_and_children_are_only_index_units(self):
        request=self.request(self.blocks());before=copy.deepcopy(request.batch)
        with patch.object(app,'embedding_models',return_value=(tiny_tokenizer(),None)):
            bundle=app.chunk_bundle(request);retry=app.chunk_bundle(request)
        self.assertEqual(bundle,retry);self.assertEqual(request.batch,before)
        self.assertEqual(len(bundle['parents']),1)
        parent=bundle['parents'][0]
        self.assertEqual(set(parent['child_ids']),{c['chunk_id'] for c in bundle['chunks']})
        self.assertTrue(all(c['token_count']<=512 and c['parent_id']==parent['parent_id'] for c in bundle['chunks']))
        self.assertEqual(len(bundle['structure']['nodes']),4)
        self.assertTrue(all(n['confidence'] is None for n in bundle['structure']['nodes']))

    def test_no_cross_section_or_cross_batch_parent_and_off_is_legacy(self):
        blocks=self.blocks();blocks[2]['section']='Different chapter'
        request=self.request(blocks)
        with patch.object(app,'embedding_models',return_value=(tiny_tokenizer(),None)):
            bundle=app.chunk_bundle(request)
            request2=request.model_copy(update={'batch_index':1});other=app.chunk_bundle(request2)
            off=request.model_copy(update={'hierarchy_enabled':False})
            legacy=app.chunk_bundle(off)
        self.assertEqual(len(bundle['parents']),3)
        self.assertFalse({p['parent_id'] for p in bundle['parents']} & {p['parent_id'] for p in other['parents']})
        self.assertEqual(set(legacy),{'chunks'})
        self.assertNotEqual(legacy['chunks'][0]['chunk_id'],bundle['chunks'][0]['chunk_id'])

    def test_bbox_bottomleft_and_word_unknown_geometry(self):
        blocks=[{'ref':'p','text':'value','section':'Chapter','label':'text','pages':[1],
            'bbox':{'l':10,'t':90,'r':30,'b':70,'coord_origin':'BOTTOMLEFT'},'page_size':{'width':100,'height':100}}]
        request=self.request(blocks);ast=ast_for(request);box=ast['nodes'][0]['bbox_refs'][0]
        self.assertEqual(box['bbox'],[10,10,30,30]);self.assertEqual(box['raw_bbox'],blocks[0]['bbox'])
        request.batch['blocks'][0].update(pages=[],bbox=None)
        word=ast_for(request)['nodes'][0]
        self.assertIsNone(word['page_start']);self.assertEqual(word['bbox_refs'],[])

    def test_ast_preview_needs_no_model(self):
        with patch.object(app,'embedding_models') as model:
            result=app.structure_preview(self.request(self.blocks()))
        model.assert_not_called();self.assertEqual(result['writes'],0)

    def test_table_cells_survive_ast_and_parent_grouping(self):
        from docling_core.types.doc import DoclingDocument
        from chunking_document import attach_chunking_document
        cells=[]
        for row in range(12):
            for col in range(2):
                cells.append(dict(text=('Column '+str(col)) if row==0 else f'value {row} {col}',
                    row_span=1,col_span=1,start_row_offset_idx=row,end_row_offset_idx=row+1,
                    start_col_offset_idx=col,end_col_offset_idx=col+1,column_header=row==0,row_header=False))
        table=dict(num_rows=12,num_cols=2,table_cells=cells)
        event={'blocks':[dict(ref='table',label='table',section='Chapter',text='original table',pages=[1],table_data=table)],
               'doclingDocument':DoclingDocument(name='table').export_to_dict()}
        attach_chunking_document(event)
        request=self.request([]).model_copy(update={'batch':event})
        with patch.object(app,'embedding_models',return_value=(tiny_tokenizer(),None)):
            bundle=app.chunk_bundle(request)
        self.assertEqual(bundle['structure']['nodes'][0]['table_structure'],table)
        self.assertTrue(all('Column' in child['content'] for child in bundle['chunks']))
        self.assertEqual(set(bundle['parents'][0]['child_ids']),{c['chunk_id'] for c in bundle['chunks']})

    def test_index_only_embeds_leaves_and_returns_structures_after_visibility(self):
        request=self.request(self.blocks());client=unittest.mock.Mock()
        client.get.side_effect=lambda **kwargs:[{'chunk_id':i} for i in kwargs['ids']]
        client.query.side_effect=lambda **kwargs:[{'chunk_id':row['chunk_id']} for row in client.upsert.call_args.kwargs['data']]
        with patch.object(app,'embedding_models',return_value=(tiny_tokenizer(),None)),patch.object(app,'encode',side_effect=lambda texts:[[1.0] for t in texts]) as encode,patch.object(app,'milvus',return_value=client),patch.object(app,'ensure_collection'):
            result=app.index(request)
        self.assertEqual(len(encode.call_args.args[0]),len(result['chunks']))
        self.assertTrue({p['parent_id'] for p in result['parents']}.isdisjoint({r['chunk_id'] for r in client.upsert.call_args.kwargs['data']}))

if __name__=='__main__':unittest.main()
