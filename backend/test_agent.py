import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch
import agent


def tool_message(code='print(2 + 2)'):
    return {'content':'先计算。','tool_calls':[{'id':'test-call','type':'function',
        'function':{'name':'run_python','arguments':json.dumps({'code':code})}}]}


class AgentTests(unittest.TestCase):
    def test_real_tool_dispatch_and_no_key_output(self):
        secret = 'example-secret-never-output-123'
        result = {'returncode':0,'timeout':False,'stdout':'4\n','stderr':'','cleaned':True}
        output = io.StringIO()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':secret}), \
             patch.object(agent, 'model_request', side_effect=[tool_message(), {'content':'答案是 4。'}]), \
             patch.object(agent, 'execute_python', return_value=result) as tool, contextlib.redirect_stdout(output):
            agent.run('2+2')
        self.assertEqual(tool.call_count, 1)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e['type'] for e in events], ['step','step','tool','result','answer'])
        self.assertNotIn(secret, output.getvalue())

    def test_never_executes_more_than_five_tools(self):
        result = {'returncode':0,'timeout':False,'stdout':'1','stderr':'','cleaned':True}
        output = io.StringIO()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'unused'}), \
             patch.object(agent, 'model_request', return_value=tool_message('print(1)')), \
             patch.object(agent, 'execute_python', return_value=result) as tool, contextlib.redirect_stdout(output):
            agent.run('test')
        self.assertEqual(tool.call_count, 5)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['type'], 'error')

    def test_failed_tool_cannot_be_presented_as_answer(self):
        failure = {'returncode':1,'timeout':False,'stdout':'','stderr':'failed','cleaned':True}
        output = io.StringIO()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':'unused'}), \
             patch.object(agent, 'model_request', side_effect=[tool_message(), {'content':'假定答案'}]), \
             patch.object(agent, 'execute_python', return_value=failure), contextlib.redirect_stdout(output):
            agent.run('test')
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertNotIn('answer', [event['type'] for event in events])
        self.assertEqual(events[-1]['type'], 'error')


if __name__ == '__main__':
    unittest.main()
