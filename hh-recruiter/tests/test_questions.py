import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
try:
 from candidate_questions import Store, Controller, ChatClient
except ImportError:
 Store=Controller=ChatClient=None

class FakeTelegram:
 def __init__(self): self.calls=[]
 def call(self, method, **data):
  self.calls.append((method,data))
  return {'message_id':len(self.calls)}

class FakeHH:
 def __init__(self): self.sent=[];self.error=False;self.fresh=True
 def verify(self,row):
  if not self.fresh: raise ValueError('changed')
 def send(self,row):
  self.sent.append((row['chat_id'],row['draft'],row['send_key']))
  if self.error: raise TimeoutError()
  return 'out-1'

class QuestionTests(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(Store,'candidate question approval subsystem is missing')
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.s=Store(Path(self.tmp.name)/'q.db');self.addCleanup(self.s.close)
  self.t=FakeTelegram();self.h=FakeHH();self.c=Controller(self.s,self.t,self.h,123)
  self.q=self.s.ingest('chat1','m1','137056588','Кандидат','Когда встреча?','2026-09-28T12:00:00+00:00','p1')
 def button(self,action='approve',user=123,chat=123):
  self.c.notify(self.q); row=self.s.get(self.q)
  return {'callback_query':{'id':'cb','from':{'id':user},'data':action+':'+row['token'],'message':{'message_id':row['telegram_message_id'],'chat':{'id':chat,'type':'private'}}}}
 def test_no_send_on_ingestion_notification(self):
  self.c.notify(self.q);self.assertEqual(self.h.sent,[]);self.assertEqual(self.s.get(self.q)['state'],'pending')
 def test_owner_approval_sends_exact_draft_once(self):
  u=self.button();expected=self.s.get(self.q)['draft'];self.c.handle(u);self.c.handle(u)
  self.assertEqual(len(self.h.sent),1);self.assertEqual(self.h.sent[0][1],expected);self.assertEqual(self.s.get(self.q)['state'],'sent')
 def test_wrong_owner_or_chat_cannot_approve(self):
  for kwargs in [{'user':999},{'chat':999}]: self.c.handle(self.button(**kwargs))
  self.assertEqual(self.h.sent,[])
 def test_rejection_blocks_old_approval(self):
  u=self.button();r=self.button('reject');self.c.handle(r);self.c.handle(u)
  self.assertEqual(self.h.sent,[]);self.assertEqual(self.s.get(self.q)['state'],'rejected')
 def test_edit_invalidates_old_button_and_requires_new_approval(self):
  old=self.button();self.s.edit(self.q,'Встреча завтра в 10:00.',1);self.c.handle(old)
  self.assertEqual(self.h.sent,[])
  self.c.handle(self.button());self.assertEqual(self.h.sent[0][1],'Встреча завтра в 10:00.')
 def test_duplicate_ingestion_preserves_decision(self):
  self.c.handle(self.button('reject'))
  q=self.s.ingest('chat1','m1','137056588','Кандидат','Когда встреча?','2026-09-28T12:00:00+00:00','p1')
  self.assertEqual(q,self.q);self.assertEqual(self.s.get(q)['state'],'rejected')
 def test_modified_question_invalidates_card(self):
  old=self.button();self.s.ingest('chat1','m1','137056588','Кандидат','Какая зарплата?','2026-09-28T12:00:00+00:00','p1');self.c.handle(old)
  self.assertEqual(self.h.sent,[]);self.assertEqual(self.s.get(self.q)['version'],2)
 def test_timeout_is_unknown_and_not_retried(self):
  self.h.error=True;u=self.button();self.c.handle(u);self.c.handle(u)
  self.assertEqual(len(self.h.sent),1);self.assertEqual(self.s.get(self.q)['state'],'unknown')
 def test_changed_hh_source_blocks_post(self):
  self.h.fresh=False;self.c.handle(self.button());self.assertEqual(self.h.sent,[]);self.assertEqual(self.s.get(self.q)['state'],'blocked')
 def test_restart_preserves_terminal_state(self):
  self.c.handle(self.button());other=Store(Path(self.tmp.name)/'q.db');self.addCleanup(other.close)
  self.assertEqual(other.get(self.q)['state'],'sent')
 def test_notification_failure_does_not_enable_approval(self):
  self.t.call=lambda *a,**k: (_ for _ in ()).throw(TimeoutError())
  with self.assertRaises(TimeoutError):self.c.notify(self.q)
  self.assertIsNone(self.s.get(self.q)['telegram_message_id']);self.assertEqual(self.h.sent,[])
 def test_plain_yes_is_not_approval(self):
  self.c.handle({'message':{'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'да'}});self.assertEqual(self.h.sent,[])
 def test_edit_reply_flow_requires_button(self):
  self.c.handle(self.button('edit'));prompt=self.t.calls[-1]
  self.c.handle({'message':{'message_id':100,'from':{'id':123},'chat':{'id':123,'type':'private'},'text':'Уточняю условия.','reply_to_message':{'message_id':len(self.t.calls)}}})
  self.assertEqual(self.s.get(self.q)['draft'],'Уточняю условия.');self.assertEqual(self.h.sent,[])
  self.c.handle(self.button());self.assertEqual(self.h.sent[0][1],'Уточняю условия.')
 def test_long_question_is_sent_in_full_before_buttons(self):
  text='абвгд '*2000;self.s.ingest('chat1','m1','137056588','Кандидат',text,'2026-09-28T12:00:00+00:00','p1');self.c.notify(self.q)
  calls=[d for m,d in self.t.calls if m=='sendMessage'];joined=''.join(d['text'] for d in calls)
  self.assertIn(text,joined);self.assertTrue(all(len(d['text'].encode('utf-16-le'))//2<=4096 for d in calls));self.assertIn('reply_markup',calls[-1])

class ChatTests(unittest.TestCase):
 def setUp(self): self.assertIsNotNone(ChatClient,'HH chat adapter missing')
 def test_current_chat_cursor_overlap_and_author_filter(self):
  class Read:
   def get(self,path,params=None):
    if path.endswith('/me'):return {'is_employer':True,'auth_type':'employer'}
    if path=='/common/chats':return {'items':[{'id':'c1','vacancy_id':'137056588','type':'NEGOTIATION'}], 'pages':1}
    first={'id':'1','type':'SIMPLE','payload':{'text':'Вопрос?'},'creation_time':'2026-09-28T12:00:00+00:00','sender_participant_id':'p','sender_display_info':{'role':'APPLICANT','name':'Тест'}}
    second={**first,'id':'2','sender_display_info':{'role':'EMPLOYER','name':'Рекрутер'}}
    return {'id':'c1','vacancy_id':'137056588','messages':[first,second] if params.get('start_message_id') else [first], 'has_more':not bool(params.get('start_message_id')), 'chat_states':{'write_message_state':{'allowed':True}}}
  client=ChatClient(Read());rows=list(client.incoming('137056588','2026-09-28T00:00:00+00:00'))
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['message_id'],'1')
 def test_cursor_that_never_advances_fails(self):
  class Read:
   def get(self,path,params=None):return {'messages':[{'id':'1'}],'has_more':True}
  with self.assertRaises(ValueError):ChatClient(Read()).messages('c1')

class FreshnessTests(unittest.TestCase):
 def row(self):return dict(chat_id='c',message_id='1',vacancy_id='137056588',participant_id='p',question='Вопрос?',draft='Ответ',send_key='a16c1982-aeea-4d40-8d08-95d665159808')
 def client(self,extra=None,allowed=True):
  source={'id':'1','payload':{'text':'Вопрос?'},'sender_display_info':{'role':'APPLICANT'},'sender_participant_id':'p'}
  class Reader:
   def get(self,path,params=None):
    if path.endswith('/me'):return {'is_employer':True,'auth_type':'employer'}
    return {'messages':[source]+([extra] if extra else []),'has_more':False,'vacancy_id':'137056588','chat_states':{'write_message_state':{'allowed':allowed}}}
  return ChatClient(Reader())
 def test_new_candidate_message_requires_review_again(self):
  with self.assertRaises(ValueError):self.client({'id':'2','sender_display_info':{'role':'APPLICANT'}}).verify(self.row())
 def test_prior_employer_reply_blocks_duplicate_answer(self):
  with self.assertRaises(ValueError):self.client({'id':'2','sender_display_info':{'role':'EMPLOYER'}}).verify(self.row())
 def test_write_forbidden_blocks_send(self):
  with self.assertRaises(ValueError):self.client(allowed=False).verify(self.row())
 def test_current_unanswered_question_passes(self):self.client().verify(self.row())
 def test_post_uses_chat_api_uuid_and_exact_text(self):
  import json
  class Response:
   status=201
   def __enter__(self):return self
   def __exit__(self,*a):pass
   def read(self):return b'{"id":"123"}'
  captured=[]
  class Opener:
   def open(self,request,timeout):captured.append(request);return Response()
  class Reader:
   token='test-not-real';user_agent='test';opener=Opener()
  row=self.row();result=ChatClient(Reader()).send(row)
  self.assertEqual(result,'123');self.assertEqual(captured[0].full_url,'https://api.hh.ru/common/chats/c/messages')
  body=json.loads(captured[0].data);self.assertEqual(body['text'],row['draft']);self.assertEqual(body['idempotency_key'],row['send_key']);self.assertTrue(body['is_automated'])
 def test_post_timeout_never_retries(self):
  calls=[]
  class Opener:
   def open(self,*a,**k):calls.append(1);raise TimeoutError()
  class Reader:
   token='test';user_agent='test';opener=Opener()
  with self.assertRaises(TimeoutError):ChatClient(Reader()).send(self.row())
  self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
