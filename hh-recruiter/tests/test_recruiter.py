import copy
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / 'scripts' if (ROOT / 'scripts').is_dir() else ROOT / 'hh-recruiter' / 'scripts'
sys.path.insert(0, str(SCRIPTS))

def load_module(name):
    spec = importlib.util.find_spec(name)
    return __import__(name) if spec else None

review = load_module('review')
hh_api = load_module('hh_api')

def fixture():
    text = ('Разработал AI-агента с API. Внедрил RAG с reranking. '
            'Лично развернул сервис в Docker на Linux. '
            'Сопровождал сервис 8 месяцев: мониторинг и откаты. '
            'Сервисом пользуются 100 сотрудников. '
            'Написал Python backend и интеграцию CRM. '
            'Настроил GitHub Actions для тестов. Снизил время обработки на 20%.')
    profile = {'vacancy_id': '137056588', 'resume_id': 'demo1',
               'professional_text': text, 'source_complete': True}
    quotes = {
        'ai_agents': 'Разработал AI-агента с API.',
        'rag': 'Внедрил RAG с reranking.',
        'production': 'Сервисом пользуются 100 сотрудников.',
        'python_backend_integrations': 'Написал Python backend и интеграцию CRM.',
        'devops_deployment': 'Лично развернул сервис в Docker на Linux.',
        'github_cicd': 'Настроил GitHub Actions для тестов.',
        'business_independence': 'Снизил время обработки на 20%.'}
    weights = [25, 20, 20, 15, 10, 5, 5]
    a = {'production_claim_supported': True,
         'scores': {k: {'points': w, 'quote': quotes[k]} for k, w in zip(quotes, weights)},
         'production_evidence': {
             'project': {'quote': quotes['ai_agents'], 'project_key': 'project1'},
             'personal_role': {'quote': quotes['devops_deployment'], 'project_key': 'project1'},
             'deployment': {'quote': quotes['devops_deployment'], 'project_key': 'project1'},
             'operations': {'quote': 'Сопровождал сервис 8 месяцев: мониторинг и откаты.', 'project_key': 'project1'},
             'users_or_result': {'quote': quotes['production'], 'project_key': 'project1'}},
         'summary': 'Учебный пример для проверки программы.',
         'questions': ['Какова ваша личная роль?', 'Как проверяли качество RAG?']}
    return profile, a

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(review, 'review module is not implemented')

    def test_supported_full_score(self):
        p, a = fixture()
        r = review.validate(p, a)
        self.assertEqual(r['score_total'], 100)
        self.assertEqual(r['status'], 'Приоритетное интервью')

    def test_missing_operations_caps_at_59(self):
        p, a = fixture()
        del a['production_evidence']['operations']
        self.assertEqual(review.validate(p, a)['score_total'], 59)

    def test_string_true_does_not_prove_production(self):
        p, a = fixture()
        a['production_claim_supported'] = 'true'
        self.assertEqual(review.validate(p, a)['score_total'], 59)

    def test_mixed_projects_do_not_prove_production(self):
        p, a = fixture()
        a['production_evidence']['operations']['project_key'] = 'different'
        self.assertEqual(review.validate(p, a)['score_total'], 59)

    def test_invented_quote_receives_zero(self):
        p, a = fixture()
        a['scores']['rag']['quote'] = 'Несуществующий опыт.'
        r = review.validate(p, a)
        self.assertEqual(r['score_total'], 80)
        self.assertEqual(r['scores']['rag']['points'], 0)

    def test_score_bounds_and_reject_boolean_points(self):
        p, a = fixture()
        a['scores']['rag']['points'] = 999
        self.assertEqual(review.validate(p, a)['score_total'], 100)
        a['scores']['rag']['points'] = True
        with self.assertRaises(ValueError):
            review.validate(p, a)

    def test_incomplete_source_never_gets_a_score(self):
        p, a = fixture()
        p['source_complete'] = False
        with self.assertRaises(ValueError):
            review.validate(p, a)

    def test_changed_resume_is_new_revision_and_repeat_is_duplicate(self):
        p, a = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'reviews.sqlite3'
            self.assertTrue(review.save(db, review.validate(p, a)))
            self.assertFalse(review.save(db, review.validate(p, a)))
            p['professional_text'] += ' Обновил резюме.'
            self.assertTrue(review.save(db, review.validate(p, a)))
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute('select count(*) from reviews').fetchone()[0], 2)
            self.assertEqual(len(review.latest(db, '137056588')), 1)

    def test_same_name_different_resume_ids_remain_distinct(self):
        p, a = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'reviews.sqlite3'
            review.save(db, review.validate(p, a))
            p['resume_id'] = 'demo2'
            review.save(db, review.validate(p, a))
            self.assertEqual(len(review.latest(db, '137056588')), 2)

    def test_formula_safe_export(self):
        p, a = fixture()
        a['summary'] = '=HYPERLINK("https://example.invalid")'
        csv_text = review.to_csv([review.validate(p, a)])
        self.assertIn("'=HYPERLINK", csv_text)

class HHTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(hh_api, 'hh_api module is not implemented')

    def test_credentials_cannot_be_sent_to_foreign_host(self):
        for url in ['https://evil.invalid/resumes/a', 'http://api.hh.ru/me',
                    'https://api.hh.ru@evil.invalid/me', 'https://api.hh.ru:443/me']:
            with self.assertRaises(ValueError):
                hh_api.official_url(url)

    def test_projection_omits_sensitive_fields_and_keeps_long_source(self):
        raw = {'id': 'r1', 'first_name': 'Hidden', 'age': 42, 'gender': {'id': 'male'},
               'contact': [{'value': 'private@example.invalid'}], 'title': 'AI Engineer',
               'skills': 'A' * 20000, 'experience': [{'position': 'Engineer',
                    'description': 'RAG production', 'company': 'Example'}]}
        p = hh_api.project_resume(raw, '137056588')
        txt = json.dumps(p)
        self.assertNotIn('Hidden', txt)
        self.assertNotIn('private@example', txt)
        self.assertNotIn('gender', txt)
        self.assertGreater(len(p['professional_text']), 20000)
        self.assertTrue(p['source_complete'])

    def test_pagination_dedup_and_resume_error_isolation(self):
        class FakeClient:
            def get(self, path, params=None):
                if path.endswith('/me'):
                    return {'is_employer': True, 'auth_type': 'employer'}
                if '/vacancies/' in path:
                    return {'id': '137056588', 'name': 'AI Engineer', 'description': 'AI'}
                if path.endswith('/negotiations'):
                    return {'collections': [{'url': 'https://api.hh.ru/negotiations/responses'}]}
                if path.endswith('/negotiations/responses'):
                    page = params['page']
                    return {'pages': 2, 'items': (
                        [{'id': 'n1', 'resume': {'id': 'r1'}}] if page == 0 else
                        [{'id': 'n1', 'resume': {'id': 'r1'}},
                         {'id': 'n2', 'resume': {'id': 'r2'}}])}
                if path.endswith('/resumes/r1'):
                    return {'id': 'r1', 'title': 'Engineer', 'skills': 'RAG'}
                if path.endswith('/resumes/r2'):
                    raise hh_api.APIError(403)
                raise AssertionError(path)
        with tempfile.TemporaryDirectory() as tmp:
            r = hh_api.collect(FakeClient(), '137056588', Path(tmp))
            self.assertEqual(r['saved'], 1)
            self.assertEqual(r['duplicate_negotiations'], 1)
            self.assertEqual(len(r['errors']), 1)
            self.assertFalse(r['complete'])
            self.assertTrue((Path(tmp) / 'r1.json').exists())

class CacheCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.p, self.a = fixture()
        self.pf, self.af = self.root / 'profile.json', self.root / 'assessment.json'

    def cli(self, *args):
        r = subprocess.run([sys.executable, str(SCRIPTS / 'review.py'), '--db',
                            str(self.root / 'db.sqlite3'), *args], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)

    def submit(self):
        self.pf.write_text(json.dumps(self.p), encoding='utf-8')
        self.af.write_text(json.dumps(self.a), encoding='utf-8')
        return self.cli('save', '--profile', str(self.pf), '--assessment', str(self.af))

    def test_duplicate_returns_exact_requested_source_version(self):
        self.a['summary'] = 'Version A'
        a = self.submit()
        self.p['professional_text'] += ' Updated B.'
        self.a['summary'] = 'Version B'
        self.submit()
        self.p['professional_text'] = fixture()[0]['professional_text']
        self.a['summary'] = 'Unused repeated assessment'
        result = self.submit()
        self.assertFalse(result['new_record'])
        self.assertEqual(result['review']['source_sha256'], a['review']['source_sha256'])
        self.assertEqual(result['review']['summary'], 'Version A')

    def test_changed_source_is_not_ranked_with_an_old_score(self):
        self.submit()
        self.p['professional_text'] += ' Updated but not assessed.'
        self.pf.write_text(json.dumps(self.p), encoding='utf-8')
        r = self.cli('rank')[0]
        self.assertIsNone(r['score_total'])
        self.assertEqual(r['source_state'], 'changed')
        self.assertFalse(r['eligible_for_ranking'])

    def test_reverted_source_selects_its_cached_assessment(self):
        self.a['summary'] = 'Version A'
        self.submit()
        self.p['professional_text'] += ' Version B.'
        self.a['summary'] = 'Version B'
        self.submit()
        self.p = fixture()[0]
        self.pf.write_text(json.dumps(self.p), encoding='utf-8')
        r = self.cli('rank')[0]
        self.assertEqual(r['summary'], 'Version A')
        self.assertEqual(r['source_state'], 'current')

    def test_missing_source_is_explicitly_excluded(self):
        self.submit()
        self.pf.unlink()
        r = self.cli('rank')[0]
        self.assertIsNone(r['score_total'])
        self.assertEqual(r['source_state'], 'missing')

if __name__ == '__main__':
    unittest.main()
