"""Live authenticated API checks; isolated account, no chat/model endpoints or file processing."""
import importlib.util,json,urllib.request,urllib.error
from pathlib import Path
spec=importlib.util.spec_from_file_location('pilot',Path(__file__).with_name('run-rag-quality-pilot.py'));p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
RUN=p.BASE/'outputs/knowledge-api-20261014'
def rejected(path,body=None,token=None,expected=403):
    try:p.api(path,body,token)
    except RuntimeError as error:
        assert str(error).startswith('HTTP '+str(expected)),str(error)
        return True
    raise AssertionError('Expected denial: '+path)
def main():
    session=p.load_session(RUN);token=session['token'];checks={}
    bases=p.api('/knowledge-bases',token=token)
    assert len([b for b in bases if b['isDefault']])==1;checks['default_base']=True
    base=p.api('/knowledge-bases',{'name':'API contract fixture','sourceModule':'integration'},token)
    first=p.api('/documents/init-upload',{'filename':'fixture-v1.pdf','size':12,'knowledgeBaseId':base['id']},token,form=True)
    second=p.api('/documents/init-upload',{'filename':'fixture-v2.pdf','size':12,'knowledgeBaseId':base['id'],'previousDocumentId':first},token,form=True)
    versions=p.api('/knowledge-bases/documents/'+first+'/versions',token=token)
    assert [v['versionNumber'] for v in versions]==[2,1];assert all(v['knowledgeBaseId']==base['id'] for v in versions)
    checks['independent_revision_ids']=first!=second
    checks['scope_membership']=True
    checks['scope_intersection']=rejected('/knowledge/search',{'knowledgeBaseIds':[bases[0]['id']],'documentIds':[first],'question':'test'},token,400)
    other=p.load_session(p.BASE/'outputs/performance-20261013')
    checks['cross_owner_base_denied']=rejected('/knowledge-bases/'+base['id']+'/documents',token=other['token'])
    checks['cross_owner_document_denied']=rejected('/knowledge/search',{'documentIds':[first],'question':'test'},other['token'])
    checks['body_identity_cannot_override_session']=rejected('/knowledge/search',{'documentIds':[first],'question':'test','userId':session['owner'],'user_context':{'user_id':session['owner'],'is_super_admin':True}},other['token'])
    checks['anonymous_denied']=rejected('/knowledge-bases',expected=401)
    checks['anonymous_search_denied']=rejected('/knowledge/search',{'documentIds':[first],'question':'test'},expected=401)
    for name,actor in [('test_owner',session),('existing_eval_owner',other)]:
        audit=p.audit(actor);assert audit['llmCalls']==0 and audit['queryTasks']==0;checks[name+'_no_llm']=audit
    p.save(RUN/'report.json',{'checks':checks,'allPassed':all(bool(x) for x in checks.values()),'fixture_note':'Two intentionally uncompleted upload records, isolated test account; no source bytes or index created.'})
    print(json.dumps({'allPassed':True,'checks':len(checks)}))
if __name__=='__main__':main()
