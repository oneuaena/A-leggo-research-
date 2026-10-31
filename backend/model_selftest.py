"""One fixed, real provider/tool exercise; reports booleans only."""
import json
from supervisor import supervise


def run_checks(key):
    events = list(supervise('计算 2+2，答案用阿拉伯数字。', key, seconds=60))
    kinds = [event.get('type') for event in events]
    outputs = [str(event.get('text','')).strip() for event in events if event.get('type') == 'result']
    answers = [str(event.get('text','')) for event in events if event.get('type') == 'answer']
    return {
        'called_python': 1 <= kinds.count('tool') <= 5,
        'real_tool_output': any(text.splitlines() and text.splitlines()[0].strip() == '4' for text in outputs),
        'real_answer': any('4' in text for text in answers),
        'no_key_in_output': key not in json.dumps(events, ensure_ascii=False),
    }
