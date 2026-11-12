import json
import multiprocessing
import sys
sys.path.insert(0,"/app")
from prescan import isolated_prescan

if __name__=="__main__":
    settings=dict(max_pages=1000,max_bytes=209715200,prescan_budget_ms=2000,prescan_max_pages=12)
    normal=isolated_prescan(sys.argv[1],settings)
    tiny=isolated_prescan(sys.argv[1],settings | {"prescan_budget_ms":1})
    assert len(normal["pages"])<=12
    assert not multiprocessing.active_children()
    print(json.dumps(dict(normalPages=len(normal["pages"]),tinyBudgetPages=len(tiny["pages"]),
                          unknownPagesAreNotClassified=True,childProcessesAfter=0)))
