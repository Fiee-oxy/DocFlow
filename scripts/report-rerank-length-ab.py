"""Summarize paired length replay with conditional candidate-pool ceilings."""
import json,math,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'evaluation/rerank-length-ab-20261029'
LABELS={'SINGLE_FACT':'单项事实','MULTI_FACT':'复合事实','LIST_CONDITIONS':'列表条件',
        'EXPLANATION_MECHANISM':'解释机制','COMPARISON_CHANGE':'比较变化'}
MODES=['TOKEN256','MODEL_CAP','FULL_COVERAGE']
def median(rows,mode):return statistics.median(r['arms'][mode]['score_ms'] for r in rows)
def count(rows,mode,k=12):return sum(r['arms'][mode]['complete_at'][str(k)] for r in rows)

def main():
    result=json.loads((ROOT/'results.json').read_text(encoding='utf8'))
    assert result.get('status')=='COMPLETE'
    cases=result['cases'];assert len(cases)==20 and all(len(c['arms'])==3 for c in cases)
    manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf8'))
    summary={'llmCalls':0,'questions':len(cases),'runtime':result['runtime'],'types':{},'all':{},'flips':[]}
    rows=['# 精排输入长度的分类型对照','',
      '固定20道既有问题、RRF候选及来源标注，5类各4题，每类中英文各2题。同一问题三组共享同一个候选池、问题、模型、标题加权和最终Top-12。是回归诊断样本，不是新盲测，也不是最终回答准确率。未修改生产参数，没有调用聊天LLM、Embedding或Milvus。','',
      '## 三组含义','',
      '- TOKEN256：当前生产逻辑，按问题词项选择临时片段，再将“问题＋片段＋特殊标记”限制为256 Token。同义词开关固定关闭。',
      '- MODEL_CAP：取消相关片段选择，直接送完整候选；超出模型有效上限时由Tokenizer裁切。**有超长候选时不能称为不截断。**',
      '- FULL_COVERAGE：上限以内的候选一次完整读取；超长候选按模型上限分窗、重叠32 Token，读取全部正文Token，取该候选各窗最高logit。运行时逐块断言Token重建完全一致；这是另一种聚合策略，不是无限长单次输入。','',
      f"本地 `{result['runtime']['model']}` 的Tokenizer上限为 {result['runtime']['tokenizer_model_max_length']}，位置表 {result['runtime']['position_table_size']}，本次有效问题/正文配对上限 {result['runtime']['effective_pair_limit']} Token。",'',
      '## 按题型：最终Top-12必要证据完整','',
      '| 题型 | 题数 | RRF池完整（可达上限） | 256 | 模型上限 | 全文覆盖 |',
      '|---|---:|---:|---:|---:|---:|']
    for category,label in LABELS.items():
        subset=[c for c in cases if c['category']==category]
        values={mode:count(subset,mode) for mode in MODES}
        summary['types'][category]={'n':len(subset),'pool_complete':sum(c['candidate_complete'] for c in subset),'top12':values,
            'median_score_ms':{mode:median(subset,mode) for mode in MODES}}
        rows.append('| '+label+' | '+str(len(subset))+' | '+str(summary['types'][category]['pool_complete'])+'/'+str(len(subset))+' | '+ ' | '.join(f"{values[m]}/{len(subset)}" for m in MODES)+' |')
    summary['all']={'pool_complete':sum(c['candidate_complete'] for c in cases),
        'top12':{mode:count(cases,mode) for mode in MODES},'median_score_ms':{mode:median(cases,mode) for mode in MODES}}
    rows+=['| 合计 | 20 | '+str(summary['all']['pool_complete'])+'/20 | '+' | '.join(str(count(cases,m))+'/20' for m in MODES)+' |','',
      '分母包含RRF已经缺证据的题，它们不能通过增大精排输入救回。每类仅4题，不据此推断某类业务普遍更适合256或更长输入。问题是解释、条件还是比较，分类来自问题表述，答案跨度仍取决于原文。','',
      '## 排名与输入保留','',
      '| 模式 | Top-1完整 | Top-3完整 | Top-12完整 | 精排输入仍含齐必要证据 |','|---|---:|---:|---:|---:|']
    for mode in MODES:
        rows.append(f"| {mode} | {count(cases,mode,1)}/20 | {count(cases,mode,3)}/20 | {count(cases,mode)}/20 | {sum(c['arms'][mode]['input_evidence_complete'] for c in cases)}/20 |")
    rows+=['','“精排输入含齐证据”是Tokenizer可见临时文本的词面诊断，不代表相关性模型必须看齐所有答案数字才能选对chunk；Top-K评分使用返回的完整chunk，不能混淆两个指标。', '',
      '## 真正不发生模型长度裁切的子集','']
    fit=[c for c in cases if c['arms']['MODEL_CAP']['overflow_model_limit']==0]
    summary['full_fit_subset']={'n':len(fit),'top12':{m:count(fit,m) for m in MODES}}
    rows.append(f"有 {len(fit)}/20 道题的整个候选池都能放入模型上限，其MODEL_CAP才是真正的一次完整读取。")
    if fit:
        rows.append('该子集Top-12：'+'；'.join(m+' '+str(count(fit,m))+'/'+str(len(fit)) for m in MODES)+'。')
        deltas=[]
        for c in fit:
            a={h['chunk_id']:h['model_rerank_score'] for h in c['arms']['MODEL_CAP']['ranked']}
            b={h['chunk_id']:h['model_rerank_score'] for h in c['arms']['FULL_COVERAGE']['ranked']}
            deltas.append(max(abs(a[k]-b[k]) for k in a))
        rows.append(f"同一完整输入在两种编码路径上的最大logit差为 {max(deltas):.8f}，用于检查分窗实现没有改变可放下的输入。")
    pairs=sum(len(c['arms']['MODEL_CAP']['raw_pair_tokens']) for c in cases)
    overflow=sum(c['arms']['MODEL_CAP']['overflow_model_limit'] for c in cases)
    lengths=[n for c in cases for n in c['arms']['MODEL_CAP']['raw_pair_tokens']]
    summary['pair_lengths']={'count':pairs,'min':min(lengths),'median':statistics.median(lengths),
        'max':max(lengths),'exceeds_256':sum(n>256 for n in lengths),'exceeds_model_limit':overflow}
    rows.append(f"总共 {pairs} 个问题/候选配对，{overflow} 个超过模型有效上限。")
    rows.append('本轮MODEL_CAP未实际裁切任何配对，可直接作为完整输入对照；FULL_COVERAGE未触发真实分窗，不能据此证明超长分窗的质量。' if overflow==0 else
        '超长配对在MODEL_CAP组裁切，在FULL_COVERAGE组分窗覆盖；只有全池可放下的子集是严格的一次不截断比较。')
    rows.append(f"原始配对长度最小/中位/最大为 {min(lengths)}/{statistics.median(lengths)}/{max(lengths)}；{sum(n>256 for n in lengths)} 个超过256 Token。")
    rows+=['','## 精排计算耗时','',
      '| 题型 | 256中位数/秒 | 模型上限中位数/秒 | 全文覆盖中位数/秒 |','|---|---:|---:|---:|']
    for category,label in LABELS.items():
        sub=[c for c in cases if c['category']==category]
        rows.append('| '+label+' | '+' | '.join(f'{median(sub,m)/1000:.3f}' for m in MODES)+' |')
    rows.append('| 合计 | '+' | '.join(f'{median(cases,m)/1000:.3f}' for m in MODES)+' |')
    rows+=['','计时从候选临时文本处理开始，到模型评分结束，包含Tokenizer、输入诊断及推理；不包含检索、RRF、Java接口、聊天LLM或模型加载。2 CPU/5GB，batch=8，模型预热后按题轮换三组执行，每组每题一次。首题曾因测试脚本不兼容新版Tokenizer中断，修复后从已保存结果续跑；不是严格重复多轮压测，不作为线上SLA。全文分窗使用最高分聚合，也可能提高长块误命中风险。','',
      '## 逐题翻转与丢失定位','']
    for c in cases:
        signatures=[c['arms'][m]['complete_at']['12'] for m in MODES]
        if len(set(signatures))>1:
            summary['flips'].append({'case_id':c['case_id'],'question':c['question'],'top12':dict(zip(MODES,signatures))})
            rows.append('- '+c['case_id']+'：'+c['question']+'；'+str(dict(zip(MODES,signatures))))
    if not summary['flips']:rows.append('本轮三组没有Top-12完整/不完整翻转；需结合Top-1/Top-3和耗时判断，不能据此证明任意长文档都适合256。')
    summary['input_loss_cases']=[];summary['first_complete_rank_changes']=[]
    for c in cases:
        short,full=c['arms']['TOKEN256'],c['arms']['MODEL_CAP']
        if full['input_evidence_complete'] and not short['input_evidence_complete']:
            summary['input_loss_cases'].append(c['case_id'])
            rows.append('- 精排临时输入未含齐全部事实，但Top-12仍完整：'+c['case_id']+' / '+c['question'])
        if short['first_complete_rank']!=full['first_complete_rank']:
            summary['first_complete_rank_changes'].append({'case_id':c['case_id'],'token256':short['first_complete_rank'],'full':full['first_complete_rank']})
            rows.append(f"- 必要证据齐全的最小排名变化：{c['case_id']}，256为 {short['first_complete_rank']}，完整输入为 {full['first_complete_rank']}。")
    for c in cases:
        if not c['candidate_complete']:rows.append('- 融合池本身缺证据：'+c['case_id']+' / '+c['question'])
    rows+=['','## 本轮取舍','']
    if not summary['flips']:
        rows.append('本轮没有发现将256改为完整输入能救回的Top-12问题，暂不支持按题型全面放大输入；这只是每类4题的固定池回归结果。最终材料齐全不等于精排临时输入保留全部事实，更不等于聊天回答必然正确。')
    else:
        rows.append(f"观察到 {len(summary['flips'])} 道题在不同模式下发生Top-12完整性翻转。应逐题核对输入丢失与排名变化，不按聚合分数直接切换默认。")
    reduction=(median(cases,'MODEL_CAP')-median(cases,'TOKEN256'))/median(cases,'MODEL_CAP')*100
    summary['median_reduction_256_vs_model_cap_percent']=reduction
    rows.append(f"256相对MODEL_CAP组的精排阶段耗时中位数变化：降低 {reduction:.1f}%（负数表示反而变慢）。它与此前整个检索接口降低26.3%的计时范围不同，不能互相替代。")
    rows+=['','## 产物与复现','',
      'manifest.json / manifest.sha256 在推理前冻结题型、问题、候选、来源和评分标注；results.json 保留每组实际可见文本、窗口数、Token数、分数、排名和耗时。summary.json 为汇总。',
      '`scripts/freeze-rerank-length-ab.py` 创建一次性输入；隔离容器 `/retrieval` 复制当前检索代码，`/work/scripts` 放评分脚本；运行 `run-rerank-length-ab.py`，可跳过已保存的题/组继续；将结果复制回本地后运行 `report-rerank-length-ab.py`。',
      '原始PDF评分继承已有来源页与原文原子口径，Word题继承已冻结原文片段；均不是独立人工语义验收，未参与本轮调参。未据此切换生产默认。']
    (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'analysis.md').write_text('\n'.join(rows)+'\n',encoding='utf8')
    print(json.dumps(summary['all'],ensure_ascii=True))
if __name__=='__main__':main()
