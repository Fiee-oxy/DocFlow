"""Opt-in bounded boundary packing; never changes an already published batch.

Complete Word paragraphs stay separate source units. Small related units may
share an extended batch, then the existing token-aware chunker decides packing.
Only explicitly identified continuation tables can become one logical table.
"""
from copy import deepcopy
import re
import unicodedata

VERSION = "docx-batch-boundary-v1"


def clean(value):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC",str(value or ""))).strip().casefold()


def caption(value):
    text=clean(value)
    match=re.match(r"^(?:续\s*)?(?:表|table)\s*([0-9]+(?:[.\-][0-9]+)*|[ivxlcdm]+)\b",text,re.I)
    if not match:
        # Chinese captions have no word boundary before the description.
        match=re.match(r"^(?:续\s*)?表\s*([0-9]+(?:[.\-][0-9]+)*)",text)
    if not match:
        return None
    continued=bool(re.search(r"^续\s*表|[（(]\s*续(?:表)?\s*[）)]|(?:[（(]\s*continued\s*[）)]|\bcontinued\s*$)",text))
    return {"id":match.group(1),"continued":continued}


def originals(block):
    if block.get("source_fragments"):
        return deepcopy(block["source_fragments"])
    return [deepcopy({k:v for k,v in block.items() if k not in {"source_fragments","docx_boundary_merge"}})]


def header(data):
    cells=data.get("table_cells",[])
    rows=0
    signature=[]
    for row in range(data.get("num_rows",0)):
        covering=[c for c in cells if c["start_row_offset_idx"]<=row<c["end_row_offset_idx"]]
        if not covering or not all(c.get("column_header") for c in covering):
            break
        rows+=1
    if not rows:
        return None
    for cell in cells:
        if cell["start_row_offset_idx"]<rows:
            if cell["end_row_offset_idx"]>rows:
                return None
            signature.append(tuple(cell.get(k) for k in ("start_row_offset_idx","end_row_offset_idx","start_col_offset_idx","end_col_offset_idx"))+(clean(cell.get("text")),))
    return rows,sorted(signature)


def render(data):
    lines=[]
    for row in range(data["num_rows"]):
        values=[""]*data["num_cols"]
        for cell in data["table_cells"]:
            if cell["start_row_offset_idx"]==row:
                values[cell["start_col_offset_idx"]]=cell["text"].replace("|","\\|").replace("\n","<br>")
        lines.append("| "+" | ".join(values)+" |")
        if row==0:
            lines.append("| "+" | ".join("---" for _ in values)+" |")
    return "\n".join(lines)


def table_decision(left,right,separator=None,max_cells=20000,max_chars=200000):
    reject=lambda reason:{"merge":False,"reason":reason}
    if left.get("label")!="table" or right.get("label")!="table":
        return reject("DIFFERENT_SOURCE_UNIT_TYPES")
    if any(b.get("images") or b.get("nested_tables") for b in (left,right)):
        return reject("TABLE_HAS_RICH_CHILD_UNITS")
    if left.get("section")!=right.get("section") or clean(left.get("section")) in {"","正文","body","text"}:
        return reject("SECTION_UNKNOWN_OR_CHANGED")
    if (left.get("source_part","word/document.xml"),left.get("part_kind"))!=(right.get("source_part","word/document.xml"),right.get("part_kind")):
        return reject("SOURCE_PART_CHANGED")
    a,b=caption(left.get("table_caption")),caption(right.get("table_caption"))
    if not a or not b or a["id"]!=b["id"] or not b["continued"]:
        return reject("NO_EXPLICIT_CONTINUATION_IDENTITY")
    nodes=[f.get("body_node") for f in originals(left) if isinstance(f.get("body_node"),int)]
    last=max(nodes,default=left.get("body_node",-100))
    expected=2 if separator is not None else 1
    if right.get("body_node",-100)-last!=expected:
        return reject("NON_ADJACENT_BODY_NODES")
    if separator is not None:
        separator_caption=caption(separator.get("text"))
        if (separator.get("body_node")!=last+1 or not separator_caption or
                separator_caption["id"]!=b["id"] or not separator_caption["continued"] or
                len(separator.get("text",""))>160):
            return reject("INTERVENING_CONTENT")
    x,y=left.get("table_data",{}),right.get("table_data",{})
    if x.get("num_cols")!=y.get("num_cols"):
        return reject("COLUMN_COUNT_CHANGED")
    hx,hy=header(x),header(y)
    if hx is None or hy is None or hx!=hy:
        return reject("EXPLICIT_HEADER_MISSING_OR_CHANGED")
    count=x["num_rows"]+y["num_rows"]-hy[0]
    if count*x["num_cols"]>max_cells or len(originals(left))+len(originals(right))+(separator is not None)>16:
        return reject("MERGE_RESOURCE_LIMIT")
    data=deepcopy(x)
    offset=x["num_rows"]-hy[0]
    for cell in y["table_cells"]:
        if cell["end_row_offset_idx"]<=hy[0]:
            continue
        copied=deepcopy(cell)
        copied["start_row_offset_idx"]+=offset
        copied["end_row_offset_idx"]+=offset
        data["table_cells"].append(copied)
    data["num_rows"]=count
    text=render(data)
    if len(text)>max_chars:
        return reject("MERGE_RESOURCE_LIMIT")
    merged=deepcopy(left)
    merged.update(table_data=data,text=text,source_fragments=originals(left)+(originals(separator) if separator else [])+originals(right))
    merged["docx_boundary_merge"]={"policy":VERSION,"kind":"EXPLICIT_CONTINUATION_TABLE",
        "source_table_ids":list(dict.fromkeys(left.get("docx_boundary_merge",{}).get("source_table_ids",[left.get("table_id")])+[right.get("table_id")])),
        "repeated_header_rows":hy[0]}
    return {"merge":True,"reason":"EXPLICIT_CONTINUATION_WITH_IDENTICAL_HEADER","block":merged}


class BoundaryPolicy:
    def __init__(self,settings,limits):
        self.enabled=settings.get("docx_boundary_aware",False)
        if not isinstance(self.enabled,bool):
            raise ValueError("DOCX_INVALID_BOUNDARY_MODE")
        self.lookahead=settings.get("docx_boundary_lookahead_nodes",8)
        if isinstance(self.lookahead,bool) or not isinstance(self.lookahead,int) or not 1<=self.lookahead<=16:
            raise ValueError("DOCX_INVALID_BOUNDARY_LOOKAHEAD")
        self.limits=limits
        self.decisions=[]
        self.merges=0

    def contract(self):
        return {"rules":VERSION,"lookaheadNodes":self.lookahead,"shortUnitChars":160,"extraChars":1200}

    def add(self,blocks,block,at_boundary):
        if not self.enabled:
            blocks.append(block);return
        separator=blocks[-1] if blocks and blocks[-1].get("label") in {"text","paragraph","caption"} and caption(blocks[-1].get("text")) else None
        if block.get("label")=="table" and separator:
            block.setdefault("table_caption",separator["text"])
        if at_boundary and blocks and block.get("label")=="table":
            candidate=blocks[-2] if separator and len(blocks)>1 else blocks[-1]
            decision=table_decision(candidate,block,separator,
                self.limits["docx_max_table_cells"],self.limits["docx_max_node_chars"])
            if len(self.decisions)<64:
                self.decisions.append({k:v for k,v in decision.items() if k!="block"})
            if decision["merge"]:
                if separator:blocks.pop()
                blocks[-1]=decision["block"];self.merges+=1;return
        blocks.append(block)

    def extend(self,blocks,nodes,chars):
        if not self.enabled or not blocks or nodes>=self.limits["docx_batch_nodes"]+self.lookahead or chars>=self.limits["docx_batch_chars"]+1200:
            return False
        last=blocks[-1]
        if last.get("part_kind") or last.get("content_kind") or last.get("images"):
            return False
        label=last.get("label")
        if label in {"title","section_header"} or caption(last.get("text")):
            return True
        if label=="table":
            return caption(last.get("table_caption")) is not None
        return label in {"text","paragraph","list_item"} and len(last.get("text",""))<=160 and clean(last.get("section")) not in {"","正文","body","text"}

    def trace(self,nodes):
        result={"policy":VERSION,"lookaheadNodes":self.lookahead,
                "extraNodes":max(0,nodes-self.limits["docx_batch_nodes"]),"tableMerges":self.merges,
                "sourceParagraphsMerged":0,"decisions":self.decisions}
        self.merges,self.decisions=0,[]
        return result
