"""Package the canonical routing dictionary for the retrieval image build context."""
from pathlib import Path
import shutil
root=Path(__file__).resolve().parents[1]
source=root/"config/retrieval-routing-rules.yml";target=root/"services/retrieval/config/retrieval-routing-rules.yml"
target.parent.mkdir(exist_ok=True)
shutil.copyfile(source,target)
print("Packaged canonical routing rules")
