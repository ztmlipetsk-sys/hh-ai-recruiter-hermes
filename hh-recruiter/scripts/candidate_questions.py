"""Owner-reviewed HH chat replies. No secrets or live actions on import."""
import argparse
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, build_opener
from urllib.error import HTTPError, URLError
from hh_api import Client, NoRedirect, identifier, employer

DEFAULT_DRAFT = 'Спасибо за вопрос. Я уточню информацию и вернусь к вам с ответом.'

def now():
    return datetime.now(timezone.utc).isoformat()

def date(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Дата должна содержать часовой пояс.')
    return result

def chunks(text, limit=3500):
    # Telegram limit counts UTF-16 code units, including supplementary emoji.
    current, size = [], 0
    for char in text:
        n = len(char.encode('utf-16-le')) // 2
        if size + n > limit:
            yield ''.join(current)
            current, size = [], 0
        current.append(char)
        size += n
    if current:
        yield ''.join(current)

class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS questions (
          id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, message_id TEXT NOT NULL,
          vacancy_id TEXT NOT NULL, candidate TEXT NOT NULL, question TEXT NOT NULL,
          created_at TEXT NOT NULL, participant_id TEXT NOT NULL, draft TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL DEFAULT 'pending',
          token TEXT, telegram_message_id INTEGER, send_key TEXT, sent_id TEXT,
          approved_at TEXT, owner_id INTEGER, UNIQUE(chat_id,message_id));
        CREATE TABLE IF NOT EXISTS edit_prompts (
          message_id INTEGER PRIMARY KEY, question_id INTEGER NOT NULL, version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, question_id INTEGER, event TEXT, at TEXT, owner_id INTEGER);
        ''')
    def close(self):
        self.db.close()
    def get(self, qid):
        r = self.db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
        if not r:
            raise ValueError('Вопрос не найден.')
        return dict(r)
    def meta(self, key, value=None):
        if value is not None:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key,str(value)))
        r = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return r[0] if r else None
    def event(self, qid, name, owner=None):
        self.db.execute('INSERT INTO audit(question_id,event,at,owner_id) VALUES (?,?,?,?)', (qid,name,now(),owner))
    def ingest(self, chat_id, message_id, vacancy_id, candidate, question, created_at, participant_id):
        for value in (chat_id,message_id,participant_id):
            identifier(value)
        identifier(vacancy_id,True)
        date(created_at)
        if not isinstance(question,str) or not question.strip():
            raise ValueError('Пустой вопрос.')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO questions(chat_id,message_id,vacancy_id,candidate,question,created_at,participant_id,draft) VALUES (?,?,?,?,?,?,?,?)', (chat_id,message_id,vacancy_id,candidate,question,created_at,participant_id,DEFAULT_DRAFT))
            row = self.db.execute('SELECT * FROM questions WHERE chat_id=? AND message_id=?',(chat_id,message_id)).fetchone()
            if (row['question'],row['candidate'],row['vacancy_id'],row['participant_id']) != (question,candidate,vacancy_id,participant_id):
                # A potentially delivered reply is never silently re-queued.
                state = 'blocked' if row['state'] in ('sending','sent','unknown') else 'pending'
                self.db.execute('UPDATE questions SET question=?,candidate=?,vacancy_id=?,participant_id=?,draft=?,version=version+1,state=?,token=NULL,telegram_message_id=NULL WHERE id=?', (question,candidate,vacancy_id,participant_id,DEFAULT_DRAFT,state,row['id']))
                self.event(row['id'],'source_changed')
            return row['id']
    def edit(self, qid, text, version):
        if not isinstance(text,str) or not 1 <= len(text.strip()) <= 20000:
            raise ValueError('Ответ должен содержать 1–20000 символов.')
        with self.db:
            changed = self.db.execute("UPDATE questions SET draft=?,version=version+1,token=NULL,telegram_message_id=NULL WHERE id=? AND version=? AND state='pending'", (text,qid,version)).rowcount
            if not changed:
                raise ValueError('Карточка устарела или уже обработана.')
            self.event(qid,'draft_changed')
    def pending(self):
        return [dict(x) for x in self.db.execute("SELECT * FROM questions WHERE state='pending' ORDER BY id")]
    def token_row(self, token):
        r = self.db.execute("SELECT * FROM questions WHERE token=? AND state='pending'", (token,)).fetchone()
        return dict(r) if r else None
    def claim(self, row, owner, state):
        with self.db:
            n=self.db.execute("UPDATE questions SET state=?,send_key=?,approved_at=?,owner_id=? WHERE id=? AND version=? AND token=? AND state='pending'", (state,str(uuid.uuid4()),now(),owner,row['id'],row['version'],row['token'])).rowcount
            if n:
                self.event(row['id'],state,owner)
        return bool(n)
    def finish(self, qid, state, sent_id=None):
        with self.db:
            self.db.execute("UPDATE questions SET state=?,sent_id=? WHERE id=? AND state='sending'",(state,sent_id,qid))
            self.event(qid,state)

class ChatClient:
    def __init__(self, reader=None):
        self.reader=reader or Client()
    def messages(self, chat_id):
        path='/common/chats/'+identifier(chat_id)+'/messages'
        cursor=None;seen=set();result=[];info=None
        for _ in range(1000):
            params={'order':'next','limit':50}
            if cursor: params['start_message_id']=cursor
            info=self.reader.get(path,params)
            items=info.get('messages')
            if not isinstance(items,list) or type(info.get('has_more')) is not bool:
                raise ValueError('Некорректный список сообщений HH.')
            for msg in items:
                mid=identifier(msg.get('id'))
                if mid not in seen: result.append(msg);seen.add(mid)
            if not info['has_more']: return result,info
            new_cursor=identifier(items[-1].get('id')) if items else None
            if not new_cursor or new_cursor==cursor:
                raise ValueError('Пагинация HH не продвигается; чтение неполное.')
            cursor=new_cursor
        raise ValueError('Превышен предел чтения сообщений.')
    def incoming(self,vacancy,since):
        employer(self.reader);vacancy=identifier(vacancy,True);since=date(since)
        seen=set()
        for page in range(51):
            data=self.reader.get('/common/chats',{'filter_with_vacancy_ids':'['+vacancy+']','page':page,'per_page':20})
            if not isinstance(data.get('items'),list) or type(data.get('pages')) is not int:
                raise ValueError('Некорректная пагинация чатов.')
            for chat in data['items']:
                cid=identifier(chat.get('id'))
                if cid in seen:continue
                seen.add(cid)
                if str(chat.get('vacancy_id'))!=vacancy or chat.get('type')!='NEGOTIATION':continue
                messages,info=self.messages(cid)
                if str(info.get('vacancy_id'))!=vacancy:raise ValueError('Изменилась вакансия чата.')
                for msg in messages:
                    author=msg.get('sender_display_info') or {}
                    if author.get('role')!='APPLICANT' or msg.get('type')!='SIMPLE':continue
                    if date(msg['creation_time'])<since:continue
                    payload=msg.get('payload') or {};text=payload.get('text')
                    if not text:
                        if payload.get('attachments'):text='[Вложение от кандидата: требуется просмотр в HH]'
                        else:continue
                    yield dict(chat_id=cid,message_id=identifier(msg['id']),vacancy_id=vacancy,candidate=author.get('name') or msg['sender_participant_id'],question=text,created_at=msg['creation_time'],participant_id=identifier(msg['sender_participant_id']))
            if page+1>=data['pages']:return
        raise ValueError('Предел страниц чатов достигнут; результат неполный.')
    def verify(self,row):
        employer(self.reader)
        messages,info=self.messages(row['chat_id'])
        if str(info.get('vacancy_id'))!=row['vacancy_id'] or info.get('chat_states',{}).get('write_message_state',{}).get('allowed') is not True:
            raise ValueError('HH не разрешает отправку в эту вакансию/чат.')
        source=next((x for x in messages if str(x['id'])==row['message_id']),None)
        if not source or source.get('sender_display_info',{}).get('role')!='APPLICANT' or source.get('sender_participant_id')!=row['participant_id'] or source.get('payload',{}).get('text')!=row['question']:
            raise ValueError('Исходный вопрос изменён, удалён или содержит вложение. Нужна проверка.')
        # A newer employer reply makes the old proposal unsafe to send blindly.
        index=messages.index(source)
        if any(x.get('sender_display_info',{}).get('role')in ('EMPLOYER','APPLICANT') for x in messages[index+1:]):
            raise ValueError('После вопроса появились новые сообщения. Нужна проверка контекста.')
    def send(self,row):
        url='https://api.hh.ru/common/chats/'+identifier(row['chat_id'])+'/messages'
        body=json.dumps({'text':row['draft'],'idempotency_key':row['send_key'],'is_automated':True}).encode()
        headers={'Authorization':'Bearer '+self.reader.token,'HH-User-Agent':self.reader.user_agent,'User-Agent':self.reader.user_agent,'Content-Type':'application/json'}
        # Exactly one POST attempt; failures are held for manual reconciliation.
        with self.reader.opener.open(Request(url,data=body,headers=headers,method='POST'),timeout=30) as response:
            if response.status!=201:raise ValueError('HH не подтвердил отправку.')
            data=json.loads(response.read().decode())
            return identifier(data.get('id'))

class Telegram:
    def __init__(self,token):
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+',token):raise ValueError('Некорректный токен отдельного бота.')
        self.base='https://api.telegram.org/bot'+token+'/'
        self.opener=build_opener(NoRedirect())
    def call(self,method,**payload):
        if method not in {'getMe','getWebhookInfo','getUpdates','sendMessage','answerCallbackQuery'}:raise ValueError('Метод Telegram не разрешён.')
        try:
            with self.opener.open(Request(self.base+method,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST'),timeout=40) as response:
                data=json.loads(response.read().decode())
            if data.get('ok') is not True:raise ValueError('Telegram отклонил запрос.')
            return data['result']
        except (HTTPError,URLError,TimeoutError) as exc:
            # Never expose a Telegram request URL: it contains the bot token.
            raise RuntimeError('Ошибка Telegram: '+type(exc).__name__) from None

class Controller:
    def __init__(self,store,telegram,hh,owner):
        if type(owner) is not int or owner<=0:raise ValueError('Нужен положительный числовой Telegram ID.')
        self.s=store;self.t=telegram;self.h=hh;self.owner=owner
    def tell(self,text,**kwargs):
        return self.t.call('sendMessage',chat_id=self.owner,text=text,**kwargs)
    def notify(self,qid):
        row=self.s.get(qid)
        if row['state']!='pending' or row['telegram_message_id']:return
        header=f"Вопрос #{qid}, версия {row['version']}\nКандидат: {row['candidate']}\nВакансия: {row['vacancy_id']}\nЧат HH: {row['chat_id']}\nДата: {row['created_at']}\nИсточник: https://hh.ru/chat\nСообщение HH: {row['message_id']}\n\nВопрос:\n"
        for part in chunks(header+row['question']):self.tell(part)
        for part in chunks('Предлагаемый ответ (можно изменить):\n'+row['draft']):self.tell(part)
        token=secrets.token_hex(16)
        result=self.tell(f"Решение по вопросу #{qid}, версия {row['version']}. Подтвердите именно показанный выше ответ.",reply_markup={'inline_keyboard':[[{'text':'Утвердить и отправить','callback_data':'approve:'+token}],[{'text':'Изменить ответ','callback_data':'edit:'+token},{'text':'Отклонить','callback_data':'reject:'+token}]]})
        mid=result.get('message_id')
        if type(mid) is not int:raise ValueError('Нет подтверждения доставки карточки Telegram.')
        with self.s.db:
            self.s.db.execute("UPDATE questions SET token=?,telegram_message_id=? WHERE id=? AND version=? AND state='pending'",(token,mid,qid,row['version']))
    def authorized(self,who,message):
        return who.get('id')==self.owner and message.get('chat',{}).get('id')==self.owner and message.get('chat',{}).get('type')=='private'
    def handle(self,update):
        cb=update.get('callback_query')
        if cb:
            msg=cb.get('message') or {}
            if not self.authorized(cb.get('from') or {},msg):return
            data=cb.get('data','').split(':',1)
            if len(data)!=2 or data[0] not in ('approve','edit','reject'):return
            action,token=data;row=self.s.token_row(token)
            if not row or row['telegram_message_id']!=msg.get('message_id'):return
            self.t.call('answerCallbackQuery',callback_query_id=cb['id'],text='Обрабатываю решение')
            if action=='edit':
                prompt=self.tell(f"Ответьте на это сообщение новым текстом ответа для вопроса #{row['id']}. После изменения потребуется новое подтверждение.",reply_markup={'force_reply':True})
                with self.s.db:self.s.db.execute('INSERT OR REPLACE INTO edit_prompts VALUES (?,?,?)',(prompt['message_id'],row['id'],row['version']))
                return
            if action=='reject':
                if self.s.claim(row,self.owner,'rejected'):self.tell(f"Вопрос #{row['id']}: отклонено, ответ не отправлен.")
                return
            if not self.s.claim(row,self.owner,'sending'):return
            row=self.s.get(row['id'])
            try:self.h.verify(row)
            except Exception:
                self.s.finish(row['id'],'blocked');self.tell(f"Вопрос #{row['id']}: отправка заблокирована проверкой HH. Проверьте доступ и актуальную переписку.");return
            try:sent_id=self.h.send(row)
            except Exception:
                self.s.finish(row['id'],'unknown');self.tell(f"Вопрос #{row['id']}: результат отправки НЕ подтверждён. Автоповтор запрещён; проверьте историю HH.");return
            self.s.finish(row['id'],'sent',sent_id);self.tell(f"Вопрос #{row['id']}: HH подтвердил отправку, ID {sent_id}.")
            return
        msg=update.get('message') or {}
        if not self.authorized(msg.get('from') or {},msg) or msg.get('forward_origin') or msg.get('forward_date'):return
        text=msg.get('text','');reply=msg.get('reply_to_message',{}).get('message_id')
        prompt=self.s.db.execute('SELECT * FROM edit_prompts WHERE message_id=?',(reply,)).fetchone()
        if prompt:
            try:self.s.edit(prompt['question_id'],text,prompt['version'])
            except ValueError:self.tell('Редактирование отклонено: карточка устарела или текст некорректен.');return
            with self.s.db:self.s.db.execute('DELETE FROM edit_prompts WHERE message_id=?',(reply,))
            self.notify(prompt['question_id']);return
        if text in ('/start','/status'):
            counts=dict(self.s.db.execute('SELECT state,count(*) FROM questions GROUP BY state').fetchall())
            self.tell('Согласование вопросов HH. Отправка — только кнопкой под конкретным ответом.\n'+json.dumps(counts,ensure_ascii=False))
        elif text=='/pending':
            # Reissue only pending cards, invalidate old buttons before delivery.
            with self.s.db:self.s.db.execute("UPDATE questions SET token=NULL,telegram_message_id=NULL WHERE state='pending'")
            for row in self.s.pending():self.notify(row['id'])

class ProcessLock:
    def __init__(self,path):self.path=path;self.file=None
    def __enter__(self):
        self.file=open(self.path,'a+b');self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise ValueError('Этот обработчик уже запущен.') from None
        return self
    def __exit__(self,*args):self.file.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check','run','status','draft'])
    p.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'workspace/questions.sqlite3')
    p.add_argument('--vacancy',default='137056588');p.add_argument('--since');p.add_argument('--question',type=int);p.add_argument('--text-file',type=Path)
    args=p.parse_args();os.umask(0o077);s=Store(args.db)
    try:
        if args.command=='status':
            print(json.dumps(dict(s.db.execute('SELECT state,count(*) FROM questions GROUP BY state').fetchall())));return 0
        if args.command=='draft':
            if not args.question or not args.text_file:raise ValueError('Нужны --question и --text-file.')
            row=s.get(args.question);s.edit(args.question,args.text_file.read_text(encoding='utf-8'),row['version']);print('Черновик обновлён; требуется новое подтверждение владельца.');return 0
        token=os.environ.get('HH_REVIEW_TELEGRAM_TOKEN','').strip();owner=int(os.environ.get('HH_REVIEW_OWNER_ID','0'))
        if not token:raise ValueError('Нужен HH_REVIEW_TELEGRAM_TOKEN отдельного бота.')
        if token==os.environ.get('TELEGRAM_BOT_TOKEN'):raise ValueError('Нельзя использовать токен работающего Hermes gateway.')
        t=Telegram(token);h=ChatClient();c=Controller(s,t,h,owner)
        bot=t.call('getMe');webhook=t.call('getWebhookInfo')
        if webhook.get('url'):raise ValueError('У этого бота установлен webhook; он не будет изменён.')
        employer(h.reader)
        if args.command=='check':print('HH и Telegram: чтение подтверждено. Ничего не отправлено.');return 0
        with ProcessLock(str(args.db)+'.lock'):
            oldbot=s.meta('bot_id')
            if oldbot and oldbot!=str(bot['id']):raise ValueError('Бот изменён: используйте отдельную базу.')
            oldowner=s.meta('owner_id')
            if oldowner and oldowner!=str(owner):raise ValueError('Владелец изменён: используйте отдельную базу.')
            s.meta('bot_id',bot['id']);s.meta('owner_id',owner)
            with s.db:s.db.execute("UPDATE questions SET state='unknown' WHERE state='sending'")
            since=s.meta('since')
            if not since:
                since=args.since or now();date(since);s.meta('since',since)
            elif args.since and args.since!=since:raise ValueError('Период уже зафиксирован; используйте отдельную базу для другого периода.')
            last_sync=0
            while True:
                if time.monotonic()-last_sync>=120:
                    try:
                        count=0
                        for item in h.incoming(args.vacancy,since):s.ingest(**item);count+=1
                        s.meta('last_sync',now());s.meta('last_sync_error','');print('HH sync completed:',count,flush=True)
                    except Exception as exc:
                        s.meta('last_sync_error',type(exc).__name__);print('HH sync failed:',type(exc).__name__,flush=True)
                        try:c.tell('Чтение HH неполное: проверьте доступ. Новые вопросы могли не загрузиться.')
                        except Exception:pass
                    last_sync=time.monotonic()
                for row in s.pending():c.notify(row['id'])
                offset=int(s.meta('offset') or '0')
                updates=t.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','callback_query'])
                for update in updates:
                    c.handle(update);s.meta('offset',update['update_id']+1)
    except KeyboardInterrupt:return 0
    except Exception as exc:
        # Only our own configuration messages are printed, never HTTP request URLs.
        print('Stopped:',type(exc).__name__)
        return 1
    finally:s.close()

if __name__=='__main__':raise SystemExit(main())
