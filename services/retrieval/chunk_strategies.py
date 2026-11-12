"""Pure chunking plugins: no model inference, storage or document-specific rules."""
import re

VERSIONS = {"legacy": "legacy-v1", "boundary": "boundary-v1"}


def boundary(text, heading, tokenizer, target, maximum):
    """Keep original character slices; prefer paragraph/sentence ends within budget."""
    reserve = len(tokenizer.encode(heading, add_special_tokens=False)) + 8
    limit = min(target, maximum - reserve)
    if limit < 16:
        raise ValueError("Title leaves fewer than 16 content tokens")
    ends = {m.end() for m in re.finditer(r"\n\s*\n|[。！？!?](?:\s*)|\.(?:\s+)|\n(?=\s*(?:[-*]|\d+[.)、]))", text)}
    start = 0
    while start < len(text):
        offsets = tokenizer(text[start:], add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
        if len(offsets) <= limit:
            yield text[start:]
            break
        hard = start + offsets[limit][0]
        floor = start + offsets[max(1, limit // 2)][0]
        end = max((p for p in ends if floor <= p <= hard), default=hard)
        if end <= start:
            raise ValueError("Tokenizer did not advance")
        yield text[start:end]
        start = end


STRATEGIES = {"boundary": boundary}
