import json
import os
import subprocess
import sys
import time

child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
with open('temporary.txt', 'w', encoding='utf-8') as file:
    file.write('test')
print(json.dumps({'type':'step', 'workspace':os.environ['ALEGGO_SESSION_ROOT'], 'child':child.pid}), flush=True)
time.sleep(60)
