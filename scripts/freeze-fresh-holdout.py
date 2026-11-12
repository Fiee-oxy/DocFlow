"""Source-grounded annotations; no retrieval, rewrite, model or pipeline calls."""
import collections,hashlib,json,re,unicodedata
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/fresh-holdout-20261112'
def norm(s):return re.sub(r'[^\w\u3400-\u9fff]','',unicodedata.normalize('NFKC',s).casefold())
# (kind, natural question, expected answer, physical page + required original atoms)
LABELS={
'zh-ai-governance-v1':[
 ('DEFINITION','按V1.0框架，模型生成看似合理却不符常理的内容属于哪类风险？','属于输出不可靠风险，框架以“幻觉”描述这种现象。',[(6,['输出不可靠风险','幻觉','看似合理','不符常理'])]),
 ('MULTI_FACT','这份框架把什么放在第一要务，又把什么作为出发点和落脚点？','促进人工智能创新发展为第一要务；有效防范化解人工智能安全风险为出发点和落脚点。',[(3,['促进人工智能创新发展','第一要务','有效防范化解人工智能安全风险','出发点','落脚点'])]),
 ('LIST_COMPLETE','第3.1.1节列出的模型算法安全风险有哪些？请列全。','可解释性差；偏见、歧视；鲁棒性弱；被窃取、篡改；输出不可靠；对抗攻击。',[(5,['可解释性差','偏见、歧视','鲁棒性弱']),(6,['被窃取、篡改','输出不可靠','对抗攻击'])]),
 ('PROCEDURE','模型算法研发者在需求分析、立项、设计及选用训练数据等关键环节，可以采取哪些措施落实伦理要求？','内部研讨、专家评议、科技伦理审查、听取公众意见、与潜在目标用户沟通交流、员工安全教育培训。',[(14,['内部研讨','专家评议','科技伦理审查','公众意见','潜在目标用户','员工安全教育培训'])]),
 ('PARAPHRASE','重点领域使用者能否完全让机器替人作决定？发生事故时需要具备什么能力？','应避免完全依赖人工智能决策，监控记录未采纳情况并分析决策不一致，具备及时切换到人工或传统系统的能力。',[(17,['避免完全依赖','监控及记录','未采纳','决策不一致','切换到人工或传统系统'])]),
],
'zh-data-security-law-2021':[
 ('FACT','这份2021年法律文本规定自哪一天施行？','2021年9月1日。',[(13,['第五十五条','2021 年9 月1 日'])]),
 ('DEFINITION','第三条分别如何界定“数据”和“数据安全”？','数据是任何以电子或者其他方式对信息的记录；数据安全强调必要措施、有效保护和合法利用的状态，以及保障持续安全状态的能力。',[(2,['电子或者其他方式','信息的记录','有效保护和合法利用','持续安全状态'])]),
 ('LIST_COMPLETE','第三条列举的数据处理活动包括哪些？','收集、存储、使用、加工、传输、提供、公开。',[(2,['收集','存储','使用','加工','传输','提供','公开'])]),
 ('CONDITION','按第二十九条，发现安全缺陷、漏洞与已经发生数据安全事件时，处理动作有什么区别？','发现缺陷漏洞等风险立即补救；发生事件立即处置，并按规定及时告知用户及向主管部门报告。',[(7,['第二十九条','立即采取补救措施','立即采取处置措施','告知用户','主管部门报告'])]),
 ('PARAPHRASE','外国执法机关来索取境内存储的数据，境内组织能否直接交付？该文本要求先满足什么条件？','非经中华人民共和国主管机关批准，不得向外国司法或执法机构提供存储于境内的数据。',[(9,['第三十六条','非经中华人民共和国主管机关批准','不得向外国司法或者执法机构','境内的数据'])]),
],
'zh-catl-esg-2024':[
 ('FACT','宁德时代2024年度报告披露的核心运营基地零碳电力比例是多少？','74.51%。',[(14,['核心运营基地零碳电力比例','74.51%'])]),
 ('MULTI_FACT','报告中的零碳战略，核心运营和价值链分别计划何时实现碳中和？','核心运营2025年、价值链2035年。',[(37,['2025 年实现核心运营碳中和','2035 年实现价值链碳中和'])]),
 ('LIST_COMPLETE','报告中不合格品管理的闭环操作由哪些环节组成？','识别、标识、隔离、评审、处置、改进。',[(22,['识别','标识','隔离','评审','处置','改进'])]),
 ('CONDITION','为避免非自愿劳动，报告对保管员工证件和工作意愿分别作了什么要求？','不得扣押政府颁发的身份证和旅行证件原件；确保所有工作属于员工自愿决定的行为。',[(52,['不得','扣押政府颁发的身份证和旅行证件的原件','员工自身自愿决定'])]),
 ('TABLE_MULTI_FACT','报告所述心成长助学在宁德、贵州毕节分别帮助多少名学生，合计多少名？','宁德88名，贵州毕节56名，合计144名。',[(61,['宁德','88 名','贵州','毕节','56 名','144 名'])]),
],
'en-nist-ai-rmf-1':[
 ('FACT','Which AI RMF function is cross-cutting and enables the other functions?','GOVERN.',[(27,['GOVERN','cross-cutting function','enables the other functions'])]),
 ('MULTI_FACT','How does the framework distinguish the questions answered by transparency, explainability and interpretability?','Transparency: what happened; explainability: how a decision was made; interpretability: why and its meaning or context to the user.',[(22,['Transparency','what happened','Explainability','how','Interpretability','why','meaning or context'])]),
 ('LIST_COMPLETE','List all seven characteristics of trustworthy AI named in Section 3.','Valid and reliable; safe; secure and resilient; accountable and transparent; explainable and interpretable; privacy-enhanced; fair with harmful bias managed.',[(17,['valid and reliable','safe','secure and resilient','accountable and transparent','explainable and interpretable','privacy-enhanced','fair with harmful bias managed'])]),
 ('CONDITION','Does the framework impose a fixed order on its functions, and what starting sequence does it describe after GOVERN?','With governance in place functions may be performed in any order; most users start with MAP after GOVERN and continue to MEASURE or MANAGE, iteratively.',[(26,['any order','After instituting','GOVERN','MAP','MEASURE or MANAGE','iterative'])]),
 ('PARAPHRASE','If an organization cannot adequately quantify or assess an AI risk, does that by itself show that the system is high-risk or low-risk?','No. Inability to appropriately measure risks does not imply that the system necessarily poses either high or low risk.',[(10,['inability to appropriately measure AI risks','does not','high or low risk'])]),
],
'en-rfc8446':[
 ('CONDITION','When may a peer send KeyUpdate, and what must happen if one arrives before Finished?','After sending Finished; a receiver seeing KeyUpdate before Finished must terminate with unexpected_message.',[(76,['KeyUpdate','after it has sent a Finished','prior to receiving a Finished','terminate the connection','unexpected_message'])]),
 ('MULTI_FACT','What two weaker security guarantees does Section 2.3 identify for TLS 1.3 early data?','0-RTT is not forward secret and has no guarantees of non-replay between connections.',[(19,['not forward secret','no guarantees of non-replay between connections'])]),
 ('LIST_COMPLETE','Absent an overriding application profile, which cipher suite MUST be implemented and which two SHOULD be implemented?','MUST TLS_AES_128_GCM_SHA256; SHOULD TLS_AES_256_GCM_SHA384 and TLS_CHACHA20_POLY1305_SHA256.',[(102,['MUST implement','TLS_AES_128_GCM_SHA256','SHOULD implement','TLS_AES_256_GCM_SHA384','TLS_CHACHA20_POLY1305_SHA256'])]),
 ('PARAPHRASE','How should an application distinguish an orderly one-way TLS shutdown from an error that aborts the connection?','close_notify indicates orderly closure of one direction and should signal end-of-data; an error alert indicates abortive closure, signals an error and prohibits further sending or receiving.',[(85,['close_notify','orderly closure of one','end-of-data','abortive closure','MUST NOT allow any','further data'])]),
 ('CONDITION','Can a TLS implementation enable early data on its own or automatically resend rejected early data?','No: enabling it requires a specific application request; rejected early data must not be automatically resent unless the application instructs it.',[(151,['MUST NOT','enable 0-RTT','specifically','requested by the application','automatically resend','unless instructed'])]),
],
'en-artemis-plan-2020':[
 ('FACT','How many test sequences were completed to certify the European Service Module propulsion system in the September 2020 plan?','51 test sequences.',[(45,['51 test sequences','European Service Module'])]),
 ('MULTI_FACT','According to this 2020 plan, was Artemis I crewed, and in what year was the Artemis II crewed flight test planned?','Artemis I was uncrewed; Artemis II crewed test was planned for 2023. These are historical plan statements.',[(15,['Artemis I','uncrewed Orion','Artemis II in 2023','crewed flight test'])]),
 ('LIST_COMPLETE','What are the three proposed primary Artemis Base Camp mission elements?','Lunar Terrain Vehicle (unpressurized rover), habitable mobility platform (pressurized rover), foundation surface habitat.',[(27,['Lunar Terrain Vehicle','unpressurized rover','habitable mobility platform','pressurized rover','foundation surface habitat'])]),
 ('EXPLANATION','Why did the 2020 plan propose integrating PPE and HALO on Earth and launching them together?','To reduce technical risk and enhance mission success by removing the need for the two segments to dock in deep space.',[(14,['Integrating PPE and','HALO on the ground','reducing technical risk','eliminating the need','dock in deep space'])]),
 ('PARAPHRASE','What operational information would partners disclose to avoid interfering with each other, and how would safety zones help?','Public location and general nature of operations inform safety-zone scale and scope; notification and coordination prevent harmful interference.',[(73,['public information','location and general nature','Safety Zones','Notification and coordination','prevent harmful interference'])]),
],
'en-euratom-2024':[
 ('FACT','How much fresh fuel was loaded into EU commercial reactors in 2024, in tonnes of uranium?','1 761 tU.',[(9,['2024','1 761 tU','fresh fuel','commercial reactors'])]),
 ('MULTI_PAGE','How many reactor units were operating in the EU-27 in 2024, and how many reactors were operating worldwide at year end?','EU-27: 101 reactor units. Worldwide at year end: 417 operating reactors.',[(9,['101 reactor units','EU-27']),(54,['417','operating reactors','end of the year'])]),
 ('TABLE_MULTI_FACT','What were the 2024 ESA spot, multiannual and MAC-3 uranium prices in EUR per kgU?','Spot 132.32; multiannual 142.26; MAC-3 155.50 EUR/kgU, for uranium contained in U3O8.',[(13,['spot','132.32','multiannual','142.26','MAC-3','155.50','EUR'])]),
 ('LIST_COMPLETE','Which five countries are named as the Sapporo 5 initiative in the report?','Canada, France, Japan, United Kingdom and United States.',[(54,['Sapporo 5','Canada','France','Japan','United Kingdom','United States'])]),
 ('PARAPHRASE','Why does the report caution a utility against bundling supplies or depending on one supplier across the fuel cycle?','It hampers market functioning and transparency, leads to excessive dependence and makes the utility vulnerable to adverse events.',[(24,['Bundling supplies','single supplier','hamper market functioning','transparency','excessive dependence','vulnerable to adverse events'])]),
],
}
SCAN=[
 ('MULTI_FACT','2024年全国地级及以上城市PM2.5平均浓度与空气质量优良天数比例分别是多少？','29.3微克/立方米；87.2%。',5,['29.3','87.2%'],'地级及以上城市细颗粒物（PM2.5）平均浓度达到29.3微克/立方米；空气质量优良天数比例为87.2%。'),
 ('TABLE_MULTI_FACT','2024年339个城市中，环境空气质量未达标的城市有多少个，占比多少？','117个，占34.5%。',11,['339','117','34.5%'],'2024年，全国339个地级及以上城市中，222个城市环境空气质量达标，占65.5%；117个城市环境空气质量超标，占34.5%。'),
 ('COMPARISON','2016至2024年，全国地表水Ⅰ～Ⅲ类水质断面比例从多少升至多少，提升多少个百分点？','从67.8%升至90.4%，提升22.6个百分点。',40,['2016','2024','67.8%','90.4%','22.6'],'2016—2024年，全国地表水Ⅰ～Ⅲ类水质断面比例由67.8%升至90.4%，上升22.6个百分点。'),
 ('TABLE_MULTI_FACT','全国耕地质量等级表中，一等地与十等地的面积、比例分别是多少？','一等地1.38亿亩、6.82%；十等地0.61亿亩、3.01%。',101,['一等地','1.38','6.82','十等地','0.61','3.01'],'表4-1 全国耕地质量等级面积及比例：一等地，1.38亿亩，6.82%；十等地，0.61亿亩，3.01%。'),
 ('MULTI_FACT','公报给出的2024年全国城市生活垃圾清运量、无害化处理量分别是多少万吨？','清运26236.62万吨，无害化处理26198.58万吨。',143,['26236.62','26198.58'],'初步核算，2024年，全国城市生活垃圾清运量26236.62万吨，无害化处理能力115.55万吨/日，无害化处理量为26198.58万吨。'),
]

def main():
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf-8'));docs={d['id']:d for d in manifest['documents']}
    assert len(docs)==8 and all(d['status']=='DOWNLOADED' and d['new_file_fingerprint'] for d in docs.values())
    cases=[];native_verified=0
    for key,labels in LABELS.items():
        pages=json.loads((OUT/'references'/(key+'.json')).read_text(encoding='utf-8'));doc=docs[key]
        for i,(kind,question,answer,units) in enumerate(labels,1):
            required=[]
            for page,atoms in units:
                text=pages[page-1]['text'];proofs=[]
                for atom in atoms:
                    assert norm(atom) in norm(text),f'Missing gold {key}:{page}:{atom}'
                    native_verified+=1
                    # Full reference page retained separately; the localized excerpt
                    # is a locator, not an assertion that lexical match proves QA.
                    proofs.append(dict(atom=atom,page=page,reference_page_sha256=hashlib.sha256(text.encode()).hexdigest()))
                required.append(dict(pages=[page],atoms=atoms,anchor_checks=proofs))
            cases.append(dict(case_id=key+f'-q{i}',document_key=key,language=doc['language'],group=doc['size_group'],kind=kind,question=question,
                expected_answer=answer,units=required,source_sha256=doc['sha256'],source_url=doc['url'],
                reference_text='\n\n'.join(pages[p-1]['text'] for p,_ in units),label_status='ASSISTANT_SOURCE_ALIGNED_PENDING_INDEPENDENT_REVIEW',
                annotation_method='REFERENCE_PAGE_READING_AND_ATOM_CHECK',answerable=True,scored=False,
                gold_locator='ONE_BASED_PHYSICAL_PAGE_NOT_CHUNK_ID',retrieval_independent=True))
    key='zh-ecology-2024';doc=docs[key]
    for i,(kind,q,answer,page,atoms,transcript) in enumerate(SCAN,1):
        image=OUT/'qa'/f'eco-{page}.png';assert image.exists()
        cases.append(dict(case_id=key+f'-q{i}',document_key=key,language='zh',group='large',kind=kind,question=q,expected_answer=answer,
            units=[dict(pages=[page],atoms=atoms)],source_sha256=doc['sha256'],source_url=doc['url'],reference_text=transcript,
            label_status='ASSISTANT_VISUAL_TRANSCRIPTION_PENDING_INDEPENDENT_REVIEW',annotation_method='PDF_PAGE_IMAGE_VISUAL_READING',
            visual_reference=str(image.relative_to(ROOT)).replace('\\','/'),visual_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            reference_text_note='Manually transcribed evidence excerpt/table cells; typography and omitted context are normalized.',
            requires_ocr=True,answerable=True,scored=False,gold_locator='ONE_BASED_PHYSICAL_PAGE_NOT_CHUNK_ID',retrieval_independent=True))
    # Candidate confusions are review aids, never hard negatives. In particular,
    # a different page can contain equivalent evidence and must not be penalized.
    for c in cases:
        for unit in c['units']:
            if not c.get('requires_ocr'):
                ref=json.loads((OUT/'references'/(c['document_key']+'.json')).read_text(encoding='utf-8'))[unit['pages'][0]-1]['text']
                unit['reference_excerpt']=ref
        c['confusion_checks']=(
            ['区分物理页与印刷页；不将图中其他年份、其他指标或相邻表格行的数字当成本题证据。'] if c['group']=='large'
            else ['区分定义、条件、例外与相邻条款；不同章节重复关键词不等于回答本题。'])
        if c['case_id']=='en-euratom-2024-q3':
            c['confusion_checks']=['区分EUR/kgU与USD/lb U3O8；区分2024值与2023比较值。2023的149.28不是2024 spot的132.32。']
            c['expected_relations']=[dict(row='spot',year=2024,value='132.32',unit='EUR/kgU'),dict(row='multiannual',year=2024,value='142.26',unit='EUR/kgU'),dict(row='MAC-3',year=2024,value='155.50',unit='EUR/kgU')]
        if c['case_id']=='en-rfc8446-q3':c['confusion_checks']=['区分MUST与SHOULD；同页digital signatures及key exchange不是cipher suites。']
        if c['case_id']=='zh-data-security-law-2021-q1':c['confusion_checks']=['施行日不是通过日；第一页通过日期不能替代第十三页施行日期。']
        if c['case_id']=='en-nist-ai-rmf-1-q2':
            c['expected_relations']=[dict(characteristic='transparency',question='what'),dict(characteristic='explainability',question='how'),dict(characteristic='interpretability',question='why')]
    cases.sort(key=lambda c:c['case_id'])
    assert len(cases)==40 and len({c['case_id'] for c in cases})==40
    assert collections.Counter(c['language']+'/'+c['group'] for c in cases)==dict.fromkeys(('zh/small','zh/large','en/small','en/large'),10)
    assert set(collections.Counter(c['document_key'] for c in cases).values())=={5}
    payload=json.dumps(cases,ensure_ascii=False,indent=2)+'\n';(OUT/'cases.json').write_text(payload,encoding='utf-8')
    queries=[{k:c[k] for k in ('case_id','document_key','language','group','kind','question')} for c in cases]
    (OUT/'queries.json').write_text(json.dumps(queries,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lock=dict(schema='fresh-document-holdout-v1',frozen_on='2026-11-12',questions=40,documents=8,scored=False,
        caseSha256=hashlib.sha256((OUT/'cases.json').read_bytes()).hexdigest(),strata=dict(collections.Counter(c['language']+'/'+c['group'] for c in cases)),
        questionKinds=dict(collections.Counter(c['kind'] for c in cases)),nativeAtomChecks=native_verified,visualCases=5,
        rule='Freeze before retrieval; corrections require a new annotation version plus audit, never change labels to fit hits.',
        documentFingerprints={k:dict(sha256=d['sha256'],bytes=d['bytes'],pages=d['pages']) for k,d in docs.items()})
    (OUT/'freeze.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(lock,ensure_ascii=True))
if __name__=='__main__':main()
