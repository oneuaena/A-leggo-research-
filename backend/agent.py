"""One isolated visitor session. The parent enforces the hard three-minute deadline."""
import json
import os
import sys
import time
import urllib.error
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sandbox import execute_python

MAX_TOOL_CALLS = 5
MAX_AGENT_SECONDS = 175
ENDPOINT = 'https://api.deepseek.com/chat/completions'


def emit(kind, **fields):
    payload = {'type': kind, **fields}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def model_request(messages, key, timeout, tool_choice='auto'):
    body = {'model':'deepseek-flash', 'thinking':{'type':'disabled'}, 'messages':messages, 'max_tokens':1200,
            'tools':[{'type':'function','function':{'name':'run_python',
                'description':'运行 Python 计算，返回标准输出和错误。最多 30 秒。',
                'parameters':{'type':'object','properties':{'code':{'type':'string'}},'required':['code']}}}],
            'tool_choice':tool_choice}
    request = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode('utf-8'), method='POST',
        headers={'Authorization':'Bearer '+key, 'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(200001)
    if len(raw) > 200000:
        raise ValueError('response too large')
    data = json.loads(raw)
    return data['choices'][0]['message']


def run(question):
    key = os.environ.get('DEEPSEEK_API_KEY', '')
    if not key:
        emit('error', text='计算服务尚未配置，请稍后再试。')
        return
    deadline = time.monotonic() + MAX_AGENT_SECONDS
    messages = [{'role':'system','content':'你是数学计算助手。用中文简要说明计划，调用 run_python 计算。代码使用 Python 标准库；每次调用用 print 输出结果。每次调用的临时环境独立，变量与文件不会保留。最多调用工具五次。给出答案并说明依据，不要假装运行过工具。'},
                {'role':'user','content':question}]
    emit('step', text='正在分析题目并拟定计算步骤。')
    calls = 0
    successful_tool = False
    for _ in range(8):
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            emit('error', text='本次计算超时，请缩小问题后重试。')
            return
        choice = 'required' if calls == 0 else ('none' if calls >= MAX_TOOL_CALLS else 'auto')
        message = model_request(messages, key, timeout=min(20, remaining), tool_choice=choice)
        content = message.get('content') or ''
        tool_calls = message.get('tool_calls') or []
        if not tool_calls:
            if calls == 0:
                emit('error', text='本次计算未能调用工具，请重试。')
                return
            if not successful_tool:
                emit('error', text='计算工具未产生可用结果，请重试。')
                return
            emit('answer', text=str(content)[:16000] or '未获得答案，请换个问题重试。')
            return
        if content:
            emit('step', text=str(content)[:4000])
        messages.append({'role':'assistant','content':content, 'tool_calls':tool_calls})
        for call in tool_calls:
            calls += 1
            if calls > MAX_TOOL_CALLS or call.get('function',{}).get('name') != 'run_python':
                emit('error', text='本次计算达到工具调用上限。')
                return
            try:
                args = json.loads(call['function'].get('arguments','{}'))
                code = args['code']
                if not isinstance(code,str) or len(code)>16000:
                    raise ValueError('invalid code')
            except Exception:
                emit('error', text='计算代码格式不正确，请重试。')
                return
            emit('tool', code=code)
            timeout = min(30, deadline-time.monotonic())
            if timeout <= 0:
                emit('error', text='本次计算超时，请重试。')
                return
            result = execute_python(code, timeout=timeout)
            if result['returncode'] == 0 and not result['timeout'] and result.get('stdout','').strip():
                successful_tool = True
            output = (result.get('stdout','') + '\n' + result.get('stderr',''))[:12000]
            if result['timeout']:
                output += '\n计算超过时间限制。'
            emit('result', text=output or ('计算完成，无输出。' if result['returncode']==0 else '计算失败，无可用输出。'))
            messages.append({'role':'tool','tool_call_id':call.get('id',''), 'content':output or '无输出'})
    emit('error', text='本次计算未能结束，请缩小问题后重试。')


if __name__ == '__main__':
    try:
        payload = json.loads(sys.stdin.readline(8193))
        question = payload.get('question')
        if not isinstance(question,str) or not question.strip() or len(question)>500:
            raise ValueError('invalid question')
        run(question.strip())
    except BaseException:
        emit('error', text='计算暂时不可用，请稍后重试。')
