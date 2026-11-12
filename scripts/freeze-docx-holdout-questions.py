"""Freeze source-based questions before parser output is examined. No chat model."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]/'evaluation/docx-holdout-20261027'
EN=[
('What are the legal maximum assisted speed and motor power for an electric cargo bike?',25,['15.5 mph','250 watts']),
('How should a rider use routes marked Walk Your Bike Only?',37,['cyclists must dismount']),
('Why are rear-loading cargo bikes easier to store in small spaces?',58,['without drastically changing the bike’s overall width']),
('Which cargo bike design offers increased stability for taking children to school?',55,['three-wheeled versions of the Long John bike','increased stability']),
('Who partnered with the city council for the free delivery trial and how long was it initially?',115,['Velocity Cycle Couriers','12-week trial']),
('Until which date was the free delivery trial extended?',118,['28 February 2025']),
('How many deliveries and miles did the program achieve in its first six months?',119,['1,100 deliveries','3,094 miles']),
('What share of delivery costs will the council pay during the extension, and who pays the rest?',120,['50% of delivery costs','participating businesses covering the remaining half']),
('How do parcel delivery arrangements differ between Phase 1 and Phase 2?',126,['delivery fees covered by each college'],128,['partnerships with national parcel carriers']),
('How many extra parcels per year will the Yodel integration allow?',128,['18,720 parcels per year']),
('What carbon savings are estimated per parcel and across all college sites?',132,['204g per parcel','102 tons']),
('What vehicle did university mail replace and what is the bike carrying capacity?',137,['replacing an electric van'],139,['150kg']),
]
ZH=[
('上海电力大学有几个本科专业，覆盖几个学科门类？',36,['本科专业40个','5个学科门类']),
('本学年本科一年级和四年级在校生各多少人？',40,['一年级2737人','四年级2509人']),
('按时点统计，普通本科生和全日制硕士研究生各有多少？',42,['普通本科生数10963','全日制3972']),
('主讲本科课程的教授比例是多少，未授课教授有几人？',66,['主讲本科课程的教授比例为95.00%','未授课的7位教授']),
('经费一览表中的生均实验经费和生均实习经费各是多少？',70,['生均实验经费689.47','生均实习经费265.14']),
('各生均面积表中，教学行政用房和实验实习场所的生均面积是多少？',77,['教学行政用房面积241255.2615.74','实验、实习场所面积83155.035.43']),
('本科实验仪器设备有多少台套，总值多少？',79,['本科教学实验仪器设备16460台（套）','2.342亿元']),
('图书馆有多少阅览座位，当年新增纸质图书多少册？',82,['阅览室座位数2645个','当年新增24719册']),
('智慧决策平台有几个主题和场景，云录播教室有多少间？',85,['10大主题，216个场景','云录播教室208间']),
('2024年停招了哪些专业？',89,['机械电子工程、材料化学、物流管理、日语']),
('本学年本科课程总共有多少门、多少门次？',93,['1666门、4573门次']),
('学校有多少省部级实验教学中心和省部级虚拟仿真实验教学项目？',80,['省部级实验教学中心2个','省部级虚拟仿真实验教学项目12个']),
]

def compact(s):
    return re.sub(r'\s+','',s)

def main():
    output=ROOT/'questions.json'
    if output.exists():raise FileExistsError('Gold is immutable; choose a new evaluation revision')
    questions=[]
    for lang,key,rows,raw in [('en','en_cargo_bikes',EN,'raw-source.json'),('zh','zh_shiep_quality',ZH,'raw-source-zh.json')]:
        original={r['node']:r for r in json.loads((ROOT/raw).read_text(encoding='utf8'))}
        for number,row in enumerate(rows,1):
            groups=[]
            for offset in range(1,len(row),2):
                node,anchors=row[offset:offset+2]
                for anchor in anchors:
                    assert compact(anchor) in compact(original[node]['text']),(key,node,anchor)
                groups.append(dict(body_node=node,source_type=original[node]['type'],anchors=anchors))
            questions.append(dict(id=f'{lang}-{number:02}',document=key,language=lang,question=row[0],necessary=groups))
    payload=dict(llmCalls=0,scope='Native visible text/table evidence; no image OCR accuracy gold',
        selection='Natural questions independently authored from raw OOXML before parser/retrieval run',questions=questions)
    output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf8')
    (ROOT/'questions.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest(),encoding='ascii')
    print('Frozen',len(questions),'questions')
if __name__=='__main__':main()
