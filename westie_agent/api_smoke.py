"""Local protocol fixture for release validation. Never uses a paid API/key."""
import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

from .api_connection import test_connection
from .bridge import ToolBridge
from .engine import LocalAgent
from .settings import Settings


def run_protocol_smoke():
    requests=[]
    dummy_key='westie-protocol-test-not-a-real-key'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            if self.path!='/v1/chat/completions' or self.headers.get('Authorization')!='Bearer '+dummy_key:
                self.send_error(400);return
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if any(key in body for key in ['think','options','keep_alive','reasoning_effort']):
                self.send_error(400);return
            names={t['function']['name'] for t in body.get('tools',[])}
            assert 'execute_action' not in names and 'approve_action' not in names
            requests.append({'path':self.path,'model':body['model'],'tool_schema_count':len(names)})
            choice=body.get('tool_choice')
            returns=[m for m in body['messages'] if m['role']=='tool']
            name=None
            arguments={}
            if isinstance(choice,dict):name='westie_connection_check';arguments={'value':'ping'}
            elif choice!='none':
                if not returns:name='get_network_snapshot'
                elif len(returns)==1:name='probe_connectivity';arguments={'target_url':None,'route':'auto','timeout_s':3}
            message={'role':'assistant','content':'连接成功，检查完成。'}
            if name:message={'role':'assistant','content':None,'tool_calls':[{'id':'call_'+str(len(requests)),'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}
            data={'id':'fixture-'+str(len(requests)),'object':'chat.completion','created':1,'model':body['model'],
                  'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if name else 'stop'}],
                  'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}
            encoded=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        settings=Settings(api_provider='compatible',api_base_url=f'http://127.0.0.1:{server.server_port}/v1',
                          model='protocol-fixture-model',tools_mode='mock',scenario_id='case_02_portal')
        connection=test_connection(settings,dummy_key)
        bridge=ToolBridge(settings)
        reply=LocalAgent(settings,bridge).reply('检查校园网认证',settings.api_base_url,lambda _:None,api_key=dummy_key)
        assert reply.diagnosis and not reply.diagnosis['model_error']
        assert reply.diagnosis['model_tool_calls']==2 and '校园账号登录' in reply.text
        return {'ok':True,'fixture_only':True,'external_api_called':False,'real_user_key_used':False,'requests':requests,
                'connection_test':connection,'agent_tool_calls':reply.diagnosis['model_tool_calls'],
                'host_completion_calls':reply.diagnosis['host_completion_calls'],'display_text':reply.text,'network_mode':'mock'}
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
