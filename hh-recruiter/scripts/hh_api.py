"""Official HH API reader for Hermes. Python standard library; GET only."""
import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

API = 'https://api.hh.ru'
PROFILE = Path(__file__).resolve().parents[1]

class APIError(RuntimeError):
    def __init__(self, status):
        self.status = status
        messages = {401: 'Нужен действующий токен работодателя HH.',
                    403: 'HH отказал в доступе: проверьте права и услугу API.',
                    404: 'Объект недоступен или удалён.', 429: 'Лимит HH; повторите позже.',
                    400: 'HH отклонил параметры или HH-User-Agent.'}
        super().__init__(messages.get(status, 'Ошибка HH API или сети.'))

def official_url(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'api.hh.ru' or p.fragment or '\\' in url:
        raise ValueError('Разрешён только https://api.hh.ru без перенаправлений.')
    return url

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

class Client:
    def __init__(self):
        self.token = os.environ.get('HH_ACCESS_TOKEN', '').strip()
        self.user_agent = os.environ.get('HH_USER_AGENT', '').strip()
        self.opener = build_opener(NoRedirect())

    def get(self, path, params=None):
        if not self.token:
            raise APIError(401)
        if not self.user_agent:
            raise ValueError('Заполните HH_USER_AGENT: название приложения и контактный email.')
        url = official_url(path if path.startswith('https://') else API + path)
        p = urlsplit(url)
        q = dict(parse_qsl(p.query))
        q.update(params or {})
        url = urlunsplit(p._replace(query=urlencode(q)))
        headers = {'Authorization': 'Bearer ' + self.token, 'HH-User-Agent': self.user_agent,
                   'User-Agent': self.user_agent, 'Accept': 'application/json'}
        for attempt in range(3):
            try:
                with self.opener.open(Request(url, headers=headers, method='GET'), timeout=30) as r:
                    if r.status != 200:
                        raise APIError(r.status)
                    body = json.loads(r.read().decode('utf-8'))
                    if not isinstance(body, dict):
                        raise ValueError('Некорректный JSON HH.')
                    return body
            except HTTPError as exc:
                if (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    try:
                        wait = min(30, max(1, float(exc.headers.get('Retry-After', 2 ** attempt))))
                    except (TypeError, ValueError):
                        wait = 2 ** attempt
                    time.sleep(wait)
                    continue
                raise APIError(exc.code) from None
            except (URLError, TimeoutError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise APIError(None) from None
        raise APIError(None)

def identifier(value, numeric=False):
    value = str(value or '')
    if not re.fullmatch(r'[0-9]+' if numeric else r'[A-Za-z0-9_-]{1,150}', value):
        raise ValueError('Некорректный ID HH.')
    return value

def clean_text(text):
    text = str(text or '')
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email removed]', text)
    text = re.sub(r'(?<!\w)(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)', '[phone removed]', text)
    lines = [x for x in text.splitlines() if not re.match(
        r'^\s*(?:возраст|дата рождения|пол|семейное положение|гражданство|национальность|религия|здоровье|age|gender|date of birth|marital status|nationality)\s*:', x, re.I)]
    return '\n'.join(lines)

def project_resume(raw, vacancy):
    rid = identifier(raw.get('id'))
    parts = ['Желаемая должность: ' + clean_text(raw.get('title', ''))]
    for i, job in enumerate(raw.get('experience') or [], 1):
        parts.extend([f'\nОпыт {i}', 'Компания: ' + clean_text(job.get('company', '')),
                      'Должность: ' + clean_text(job.get('position', '')),
                      'Период работы: ' + str(job.get('start') or '') + ' — ' + str(job.get('end') or 'по настоящее время'),
                      clean_text(job.get('description', ''))])
    parts += ['\nНавыки: ' + ', '.join(clean_text(x) for x in raw.get('skill_set') or []),
              '\nПрофессиональное описание:\n' + clean_text(raw.get('skills', ''))]
    text = '\n'.join(parts)
    return {'resume_id': rid, 'vacancy_id': identifier(vacancy, True),
            'source_url': 'https://hh.ru/resume/' + rid,
            'fetched_at': datetime.now(timezone.utc).isoformat(), 'source_complete': True,
            'professional_text': text,
            'professional_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'privacy_note': 'Структурные личные поля исключены. Свободный текст требует проверки; полнота означает отсутствие обрезки профессиональных разделов.'}

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

def pages(client, url, params=None, max_pages=1000):
    for page in range(max_pages):
        data = client.get(url, {**(params or {}), 'page': page, 'per_page': 50})
        items, count = data.get('items'), data.get('pages')
        if not isinstance(items, list) or type(count) is not int or count < 0:
            raise ValueError('HH вернул некорректную пагинацию.')
        for item in items:
            if not isinstance(item, dict):
                raise ValueError('HH вернул некорректную карточку.')
            yield item
        if page + 1 >= count:
            return
    raise ValueError('Предел страниц достигнут; выгрузка неполная.')

def employer(client):
    me = client.get(API + '/me')
    if me.get('is_employer') is not True or me.get('auth_type') != 'employer':
        raise APIError(403)
    return me

def collect(client, vacancy, out):
    vid = identifier(vacancy, True)
    employer(client)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / '_vacancy.json', client.get(API + '/vacancies/' + vid))
    root = client.get(API + '/negotiations', {'vacancy_id': vid})
    if not isinstance(root.get('collections'), list):
        raise ValueError('Не получены коллекции откликов HH.')
    seen_n, seen_r = set(), set()
    result = {'vacancy_id': vid, 'saved': 0, 'unchanged': 0, 'duplicate_negotiations': 0,
              'duplicate_resumes': 0, 'errors': [], 'complete': True}
    for collection in root['collections']:
        try:
            url = official_url(collection['url'])
            split = urlsplit(url)
            if not split.path.startswith('/negotiations/'):
                raise ValueError('Некорректный путь коллекции.')
            params = {k: v for k, v in parse_qsl(split.query) if k == 'order_by'}
            params['vacancy_id'] = vid
            url = urlunsplit(split._replace(query=''))
            for item in pages(client, url, params):
                nid = identifier(item.get('id'))
                if nid in seen_n:
                    result['duplicate_negotiations'] += 1
                    continue
                seen_n.add(nid)
                try:
                    resume = item.get('resume') or {}
                    rid = identifier(resume.get('id'))
                    if rid in seen_r:
                        result['duplicate_resumes'] += 1
                        continue
                    target = resume.get('url') or API + '/resumes/' + rid
                    official_url(target)
                    if urlsplit(target).path.rstrip('/') != '/resumes/' + rid:
                        raise ValueError('URL и ID резюме различаются.')
                    p = project_resume(client.get(target), vid)
                    if p['resume_id'] != rid:
                        raise ValueError('HH вернул другой ID резюме.')
                    p['negotiation_id'] = nid
                    dest = out / (rid + '.json')
                    old = json.loads(dest.read_text(encoding='utf-8')) if dest.exists() else {}
                    unchanged = old.get('professional_sha256') == p['professional_sha256']
                    write_json(dest, p)
                    result['unchanged' if unchanged else 'saved'] += 1
                    seen_r.add(rid)
                except (APIError, ValueError, KeyError, OSError, TypeError) as exc:
                    result['errors'].append({'negotiation_id': nid, 'error': str(exc) if isinstance(exc, APIError) else type(exc).__name__})
        except (APIError, ValueError, KeyError, OSError, TypeError) as exc:
            result['errors'].append({'collection': True, 'error': str(exc) if isinstance(exc, APIError) else type(exc).__name__})
    result['complete'] = not result['errors']
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    write_json(out / '_sync_status.json', result)
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    p = commands.add_parser('sync')
    p.add_argument('--vacancy', default='137056588')
    p.add_argument('--out')
    p = commands.add_parser('resume')
    p.add_argument('--id', required=True)
    p.add_argument('--vacancy', default='137056588')
    p.add_argument('--out')
    p = commands.add_parser('search')
    p.add_argument('--query', required=True)
    p.add_argument('--page', type=int, default=0)
    p.add_argument('--out', required=True)
    args = parser.parse_args()
    client = Client()
    try:
        if args.command == 'check':
            employer(client)
            result = {'employer_authorized': True, 'endpoint_entitlements': 'Проверяются при выполнении sync/search.'}
        elif args.command == 'sync':
            result = collect(client, args.vacancy, Path(args.out) if args.out else PROFILE / 'workspace' / 'inbox' / args.vacancy)
        elif args.command == 'resume':
            employer(client)
            rid = identifier(args.id)
            result = project_resume(client.get(API + '/resumes/' + rid), args.vacancy)
            out = Path(args.out) if args.out else PROFILE / 'workspace' / 'inbox' / args.vacancy / (rid + '.json')
            write_json(out, result)
            result = {'saved': str(out), 'resume_id': rid}
        else:
            employer(client)
            if args.page < 0:
                raise ValueError('Номер страницы должен быть неотрицательным.')
            body = client.get(API + '/resumes', {'text': args.query, 'page': args.page, 'per_page': 50})
            result = {'found': body.get('found'), 'pages': body.get('pages'), 'page': args.page,
                      'items': [{'resume_id': identifier(x.get('id')), 'title': clean_text(x.get('title'))}
                                for x in body.get('items', [])]}
            write_json(args.out, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('complete') is False else 0
    except (APIError, ValueError, OSError, KeyError, TypeError) as exc:
        msg = str(exc) if isinstance(exc, (APIError, ValueError)) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print(json.dumps({'error': msg}, ensure_ascii=False))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
