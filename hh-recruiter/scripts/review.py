"""Local evidence validation and arithmetic; no model calls and no HH writes."""
import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

WEIGHTS = {'ai_agents': 25, 'rag': 20, 'production': 20,
           'python_backend_integrations': 15, 'devops_deployment': 10,
           'github_cicd': 5, 'business_independence': 5}
DIMENSIONS = ('project', 'personal_role', 'deployment', 'operations', 'users_or_result')
VERSION = 'hermes-hh-1.0-rubric3'
PROFILE = Path(__file__).resolve().parents[1]

def source_hash(text):
    return hashlib.sha256(text.replace('\r\n', '\n').encode('utf-8')).hexdigest()

def exact_quote(item, source):
    quote = item.get('quote') if isinstance(item, dict) else None
    return isinstance(quote, str) and len(quote.strip()) >= 10 and quote in source

def validate(profile, assessment):
    source = profile.get('professional_text')
    if profile.get('source_complete') is not True or not isinstance(source, str) or not source.strip():
        raise ValueError('Нет полного профессионального текста: оценка не создаётся.')
    vid, rid = str(profile.get('vacancy_id', '')), str(profile.get('resume_id', ''))
    if not re.fullmatch(r'[0-9]+', vid) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}', rid):
        raise ValueError('Нужны корректные vacancy_id и resume_id.')
    if not isinstance(assessment, dict) or not isinstance(assessment.get('scores'), dict):
        raise ValueError('Нужен объект scores из образца assessment.')
    scores, notes = {}, []
    for name, maximum in WEIGHTS.items():
        item = assessment['scores'].get(name, {})
        if not isinstance(item, dict) or type(item.get('points', 0)) is not int:
            raise ValueError('Баллы должны быть целыми числами, не boolean.')
        points = max(0, min(maximum, item.get('points', 0)))
        supported = exact_quote(item, source)
        if points and not supported:
            notes.append(name + ': нет точной цитаты, балл обнулён')
        scores[name] = {'points': points if supported else 0,
                        'quote': item.get('quote', '') if supported else ''}
    ev = assessment.get('production_evidence', {})
    if not isinstance(ev, dict):
        ev = {}
    verified = {k: ev[k] for k in DIMENSIONS if exact_quote(ev.get(k), source)}
    keys = {str(v.get('project_key', '')).strip() for v in verified.values()}
    proven = (assessment.get('production_claim_supported') is True
              and len(verified) == 5 and len(keys) == 1 and '' not in keys)
    raw = sum(x['points'] for x in scores.values())
    total = raw if proven else min(raw, 59)
    if proven and total >= 80:
        status = 'Приоритетное интервью'
    elif proven and total >= 65:
        status = 'Интервью'
    elif total >= 50:
        status = 'Проверить вручную'
    else:
        status = 'Низкое соответствие по доступным сведениям'
    if not proven:
        notes.append('Опыт production не подтверждён по пяти измерениям; предел 59. Это не отказ.')
    summary = assessment.get('summary', '')
    questions = assessment.get('questions', [])
    if not isinstance(summary, str) or not isinstance(questions, list) or not all(isinstance(x, str) for x in questions):
        raise ValueError('summary должен быть текстом, questions — списком текстов.')
    return {'vacancy_id': vid, 'resume_id': rid, 'rubric_version': VERSION,
            'source_sha256': source_hash(source), 'evaluated_at': datetime.now(timezone.utc).isoformat(),
            'score_raw': raw, 'score_total': total, 'production_supported_in_source': proven,
            'scores': scores, 'production_evidence': verified, 'status': status,
            'summary': summary, 'questions': questions, 'validation_notes': notes,
            'independently_verified': False, 'human_decision_required': True}

def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.execute('''CREATE TABLE IF NOT EXISTS reviews (
        id INTEGER PRIMARY KEY, vacancy_id TEXT NOT NULL, resume_id TEXT NOT NULL,
        source_sha TEXT NOT NULL, rubric TEXT NOT NULL, payload TEXT NOT NULL,
        UNIQUE(vacancy_id,resume_id,source_sha,rubric))''')
    con.execute('''CREATE TABLE IF NOT EXISTS current_sources (
        vacancy_id TEXT NOT NULL, resume_id TEXT NOT NULL, source_file TEXT NOT NULL,
        PRIMARY KEY(vacancy_id,resume_id))''')
    con.commit()
    return con

def save(path, result):
    con = database(path)
    try:
        with con:
            cur = con.execute('INSERT OR IGNORE INTO reviews(vacancy_id,resume_id,source_sha,rubric,payload) VALUES (?,?,?,?,?)',
                (result['vacancy_id'], result['resume_id'], result['source_sha256'], result['rubric_version'],
                 json.dumps(result, ensure_ascii=False)))
            inserted = cur.rowcount == 1
            if result.get('source_file'):
                con.execute('''INSERT INTO current_sources VALUES (?,?,?)
                    ON CONFLICT(vacancy_id,resume_id) DO UPDATE SET source_file=excluded.source_file''',
                    (result['vacancy_id'], result['resume_id'], result['source_file']))
            return inserted
    finally:
        con.close()

def latest(path, vacancy):
    con = database(path)
    try:
        rows = con.execute('SELECT payload FROM reviews WHERE vacancy_id=? AND rubric=? ORDER BY id',
                           (str(vacancy), VERSION)).fetchall()
        paths = dict(con.execute('SELECT resume_id,source_file FROM current_sources WHERE vacancy_id=?',
                                 (str(vacancy),)).fetchall())
    finally:
        con.close()
    groups = {}
    for row in rows:
        r = json.loads(row[0])
        groups.setdefault(r['resume_id'], []).append(r)
    results = []
    for rid, versions in groups.items():
        result = versions[-1]
        filename = paths.get(rid) or result.get('source_file')
        state, digest = 'not_checked', None
        if filename:
            try:
                p = json.loads(Path(filename).read_text(encoding='utf-8-sig'))
                if (p.get('source_complete') is not True or not isinstance(p.get('professional_text'), str)
                        or not p['professional_text'].strip() or str(p.get('vacancy_id')) != str(vacancy)
                        or p.get('resume_id') != rid):
                    raise ValueError('invalid source')
                digest = source_hash(p['professional_text'])
                state = 'changed'
            except FileNotFoundError:
                state = 'missing'
            except (OSError, ValueError, AttributeError, TypeError):
                state = 'unreadable_or_incomplete'
        if digest:
            match = next((r for r in reversed(versions) if r['source_sha256'] == digest), None)
            if match:
                result, state = match, 'current'
        result = dict(result)
        result['source_state'] = state
        result['source_file'] = filename
        result['eligible_for_ranking'] = state == 'current'
        if state != 'current':
            result['previous_score_total'] = result['score_total']
            result['score_total'] = None
            result['status'] = 'Требуется проверка актуального источника'
            result['previous_summary'] = result['summary']
            result['summary'] = 'Историческая оценка исключена из рейтинга: ' + state
        results.append(result)
    return sorted(results, key=lambda x: (not x['eligible_for_ranking'], -(x['score_total'] or 0), x['resume_id']))

def cached(path, result):
    con = database(path)
    try:
        row = con.execute('SELECT payload FROM reviews WHERE vacancy_id=? AND resume_id=? AND source_sha=? AND rubric=?',
            (result['vacancy_id'], result['resume_id'], result['source_sha256'], result['rubric_version'])).fetchone()
        if row is None:
            raise ValueError('Оценка данной версии источника не найдена.')
        return json.loads(row[0])
    finally:
        con.close()

def to_csv(rows):
    buf = io.StringIO(newline='')
    writer = csv.writer(buf)
    writer.writerow(['ID резюме', 'Балл', 'Production по тексту', 'Статус', 'Вывод', 'Дата', 'Рубрика',
                     'Актуальность источника', 'Исторический балл'])
    for r in rows:
        values = [r['resume_id'], r['score_total'], r['production_supported_in_source'],
                  r['status'], r['summary'], r['evaluated_at'], r['rubric_version'],
                  r.get('source_state', ''), r.get('previous_score_total', '')]
        writer.writerow(["'" + str(v) if str(v).lstrip().startswith(('=', '+', '-', '@', '\t', '\r'))
                         else v for v in values])
    return buf.getvalue()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=str(PROFILE / 'workspace' / 'recruiter.sqlite3'))
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('save')
    p.add_argument('--profile', required=True)
    p.add_argument('--assessment', required=True)
    p = commands.add_parser('rank')
    p.add_argument('--vacancy', default='137056588')
    p.add_argument('--csv')
    args = parser.parse_args()
    try:
        if args.command == 'save':
            p = json.loads(Path(args.profile).read_text(encoding='utf-8-sig'))
            a = json.loads(Path(args.assessment).read_text(encoding='utf-8-sig'))
            result = validate(p, a)
            result['source_file'] = str(Path(args.profile).resolve())
            inserted = save(args.db, result)
            if not inserted:
                result = cached(args.db, result)
            print(json.dumps({'new_record': inserted, 'review': result}, ensure_ascii=False, indent=2))
        else:
            rows = latest(args.db, args.vacancy)
            if args.csv:
                Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
                Path(args.csv).write_text(to_csv(rows), encoding='utf-8-sig', newline='')
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, StopIteration) as exc:
        msg = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        print(json.dumps({'error': msg}, ensure_ascii=False))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
