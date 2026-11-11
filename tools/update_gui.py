#!/usr/bin/env python3
"""서원대학교 규정집 데이터 업데이트 프로그램.

'업데이트 시작' 버튼 한 번으로
  1) 서원대학교 홈페이지 규정집(www.seowon.ac.kr)에서 규정 HWP 수집·본문 추출 → rules.json
  2) 국가법령정보센터 Open API로 인용 법령 확인               → laws.json
  3) 내용이 바뀐 파일만 GitHub 저장소(main 브랜치)의 data/에 업로드
를 차례로 실행한다. 모바일 앱은 새 데이터가 올라오면 열 때 자동으로 반영한다.

국가법령정보센터 API는 OPEN API 신청 때 등록한 PC(공인 IP)에서만 접속되므로, 이 프로그램은
그 PC에서 실행한다. 설정(인증키·토큰)은 이 PC의 사용자 폴더에만 저장된다.

실행: 같은 폴더의 '규정업데이트.bat'을 더블클릭 (또는 python update_gui.py)
"""
import base64
import shutil
import datetime as dt
import json
import os
import queue
import re
import subprocess
import sys
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), 'seowon-rules')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
WORK_DIR = os.path.join(CONFIG_DIR, 'data')

DEFAULTS = {
    'oc': '',               # 국가법령정보센터 Open API 인증키
    'token': '',            # GitHub 토큰 (이 저장소 Contents 읽기·쓰기 권한)
    'repo': '',             # GitHub 저장소 '아이디/저장소이름' (처음 실행할 때 화면에서 입력)
    'branch': 'main',       # GitHub Pages로 공개하는 브랜치
    'data_path': 'data',    # 저장소 안의 데이터 폴더
    'tools_path': 'tools',  # 저장소 안의 프로그램 폴더
    'check_laws': True,
    'update_tools': True,   # 실행 전에 저장소의 최신 수집·확인 프로그램 받기
    'api': 'https://api.github.com',
    'crawler_args': [],     # 수집 프로그램에 덧붙일 옵션(예: ["--scan", "1-600"])
    'law_args': [],         # 법령 확인 프로그램에 덧붙일 옵션
}
TOOLS = ('seowon_rules_crawler.py', 'hwp5text.py', 'law_check.py')
MIN_RATIO = 0.8  # 수집 결과가 기존 규정 수의 80% 미만이면 업로드하지 않는다(수집 실패 방지)
MAX_THIN_RATIO = 0.05  # 본문이 거의 없는 규정이 5%를 넘으면 업로드하지 않는다(check_data.js와 같은 기준)


class StepError(Exception):
    pass


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


# ---------------- GitHub ----------------

class GitHub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg['api'].rstrip('/')

    def _req(self, method, path, body=None, raw_path=False):
        if raw_path:  # git 데이터 API(/git/...)
            url = f"{self.base}/repos/{self.cfg['repo']}/{path}"
        else:
            url = f"{self.base}/repos/{self.cfg['repo']}/contents/{path}"
            if method == 'GET':
                url += f"?ref={self.cfg['branch']}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'Authorization': f"Bearer {self.cfg['token']}",
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': 'seowon-rules-updater',
            **({'Content-Type': 'application/json'} if data else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == 'GET':
                return None
            msg = e.read().decode(errors='replace')[:300]
            if e.code in (401, 403):
                raise StepError(f'GitHub 권한 오류({e.code}): 토큰이 맞는지, 이 저장소의 Contents 쓰기 권한이 있는지 확인해 주세요. {msg}')
            raise StepError(f'GitHub 오류({e.code}): {msg}')
        except urllib.error.URLError as e:
            raise StepError(f'GitHub에 연결하지 못했습니다: {e.reason}')

    def read(self, name):
        """저장소의 파일 → (sha, 내용 bytes). 없으면 (None, None)"""
        path = f"{self.cfg['data_path']}/{name}"
        meta = self._req('GET', path)
        if not meta:
            return None, None
        if meta.get('content'):
            return meta['sha'], base64.b64decode(meta['content'])
        # 1MB가 넘는 파일은 내용 없이 오므로 원본 주소로 받는다
        req = urllib.request.Request(meta['download_url'], headers={'User-Agent': 'seowon-rules-updater',
                                                                     'Authorization': f"Bearer {self.cfg['token']}"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return meta['sha'], r.read()

    def write(self, name, content, sha, message):
        body = {'message': message, 'content': base64.b64encode(content).decode(), 'branch': self.cfg['branch']}
        if sha:
            body['sha'] = sha
        return self._req('PUT', f"{self.cfg['data_path']}/{name}", body)

    def commit_files(self, files, message):
        """여러 파일({저장소 경로: bytes})을 커밋 하나로 올린다."""
        br = self.cfg['branch']
        ref = self._req('GET', f'git/ref/heads/{br}', raw_path=True)
        if not ref:
            raise StepError(f'{br} 브랜치를 찾지 못했습니다.')
        head = ref['object']['sha']
        base_tree = self._req('GET', f'git/commits/{head}', raw_path=True)['tree']['sha']
        tree = []
        for path, content in files.items():
            blob = self._req('POST', 'git/blobs', {'content': base64.b64encode(content).decode(), 'encoding': 'base64'}, raw_path=True)
            tree.append({'path': path, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        t = self._req('POST', 'git/trees', {'base_tree': base_tree, 'tree': tree}, raw_path=True)
        c = self._req('POST', 'git/commits', {'message': message, 'tree': t['sha'], 'parents': [head]}, raw_path=True)
        self._req('PATCH', f'git/refs/heads/{br}', {'sha': c['sha']}, raw_path=True)

    def raw(self, path):
        """저장소의 다른 파일(프로그램) 받기"""
        sha, content = GitHub(dict(self.cfg, data_path=os.path.dirname(path))).read(os.path.basename(path))
        return content


# ---------------- 비교 ----------------

def rules_summary(old, new):
    """규정 데이터 변경 요약: 추가·삭제·변경된 규정명"""
    o = {r['title']: r.get('text', '') for r in (old or {}).get('rules', [])}
    n = {r['title']: r.get('text', '') for r in new.get('rules', [])}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = sorted(t for t in set(n) & set(o) if n[t] != o[t])
    return added, removed, changed


MAX_EVENTS = 30        # changes.json에 남길 업데이트 횟수
MAX_TEXT_RULES = 60    # 한 번에 이보다 많이 바뀌면(수집 형식 변경 등) 본문 없이 이름만 기록


def change_event(old, new, at):
    """이번 업데이트에서 바뀐 규정의 이전·이후 본문. 앱이 조문별 신구대비표를 만든다."""
    o = {r['title']: r for r in (old or {}).get('rules', [])}
    n = {r['title']: r for r in new.get('rules', [])}
    added, removed, changed = rules_summary(old, new)
    items = [{'title': t, 'kind': 'added', 'after': n[t].get('text', '')} for t in added]
    items += [{'title': t, 'kind': 'removed', 'before': o[t].get('text', '')} for t in removed]
    items += [{'title': t, 'kind': 'changed', 'before': o[t].get('text', ''), 'after': n[t].get('text', '')} for t in changed]
    event = {'at': at, 'added': len(added), 'removed': len(removed), 'changed': len(changed), 'rules': items}
    if len(items) > MAX_TEXT_RULES:
        event['rules'] = [{'title': i['title'], 'kind': i['kind']} for i in items]
        event['note'] = f'바뀐 규정이 {len(items)}건으로 많아 본문 비교는 저장하지 않았습니다(수집 형식이 바뀌었을 수 있음).'
    return event


def same_rules(old, new):
    a, r, c = rules_summary(old, new)
    if a or r or c:
        return False
    oc = {r['title']: (r.get('category', ''), r.get('attachments')) for r in old.get('rules', [])}
    return all(oc.get(r['title']) == (r.get('category', ''), r.get('attachments')) for r in new.get('rules', []))


def _art_sort(k):
    return [int(x) if x.isdigit() else 0 for x in str(k).split('-')]


def law_text_diff(old_arts, new_arts):
    """법령 전체 조문 두 판 → 바뀐 조 [{key, no, title, status, before, after}]"""
    norm_t = lambda t: re.sub(r'\s+', ' ', t or '').strip()
    rows = []
    for k in sorted(set(old_arts) | set(new_arts), key=_art_sort):
        a, b = old_arts.get(k), new_arts.get(k)
        if a and b and norm_t(a.get('text')) == norm_t(b.get('text')):
            continue
        cur = b or a
        status = 'added' if not a else 'removed' if not b else 'deleted' if b.get('deleted') and not a.get('deleted') else 'modified'
        rows.append({'key': k, 'no': cur.get('no', ''), 'title': cur.get('title', ''), 'status': status,
                     'before': (a or {}).get('text', ''), 'after': (b or {}).get('text', '')})
    return rows


def law_change_event(old_laws, new_laws, old_text, new_text, at):
    """상위법 개정 알림: 전체 조문 해시가 바뀐 법령마다 바뀐 조를 기록한다.
    old_text/new_text: 법령 항목 → 조문 dict(없으면 None)"""
    old_map = (old_laws or {}).get('laws', {})
    laws = []
    for k, v in new_laws.get('laws', {}).items():
        o = old_map.get(k)
        if not o or not o.get('textHash') or not v.get('textHash') or o['textHash'] == v['textHash']:
            continue
        before, after = old_text(o), new_text(v)
        if before is None or after is None:
            continue
        rows = law_text_diff(before, after)
        if not rows:
            continue
        # 크게 바뀐 법령(전부개정 등)은 우리 규정이 인용한 조와 앞의 40개 조만 본문을 남긴다(파일 크기)
        cited = set(v.get('articles') or [])
        if len(rows) > 40:
            for i, r in enumerate(rows):
                if i >= 40 and r['key'] not in cited:
                    r['before'] = r['after'] = ''
                    r['trimmed'] = True
        laws.append({'key': k, 'name': v['name'], 'type': v.get('type', ''), 'url': v.get('url', ''),
                     'enforced': v.get('enforced', ''), 'prevEnforced': o.get('enforced', ''),
                     'promulgated': v.get('promulgated', ''), 'from': v.get('from', []), 'articles': rows})
    return {'at': at, 'laws': laws} if laws else None


def same_laws(old, new):
    return (old or {}).get('laws') == new.get('laws')


# ---------------- 실행 ----------------

def run_tool(args, log, stop):
    """수집·확인 프로그램을 실행하며 출력 줄을 log로 넘긴다."""
    cmd = [sys.executable, '-u'] + args
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=WORK_DIR, env=env,
                         creationflags=flags)
    for raw in iter(p.stdout.readline, b''):
        log(raw.decode('utf-8', errors='replace').rstrip())
        if stop.is_set():
            p.terminate()
            raise StepError('사용자가 중지했습니다.')
    p.wait()
    if p.returncode != 0:
        raise StepError(f'{os.path.basename(args[0])} 실행이 실패했습니다(코드 {p.returncode}). 위 기록을 확인해 주세요.')


def update(cfg, log, stop=None):
    """전체 업데이트. 성공하면 결과 요약 문자열을 돌려준다."""
    stop = stop or threading.Event()
    if not cfg.get('repo') or '/' not in cfg['repo']:
        raise StepError("GitHub 저장소를 '아이디/저장소이름' 형식으로 입력해 주세요.")
    if not cfg.get('token'):
        raise StepError('GitHub 토큰을 먼저 설정해 주세요.')
    if cfg.get('check_laws') and not cfg.get('oc'):
        raise StepError('국가법령정보센터 인증키(OC)를 설정하거나 "법령 확인"을 꺼 주세요.')
    os.makedirs(WORK_DIR, exist_ok=True)
    gh = GitHub(cfg)

    # 0) 최신 프로그램 받기
    tool_dir = HERE
    if cfg.get('update_tools'):
        log('■ 최신 수집·확인 프로그램을 받는 중…')
        try:
            for t in TOOLS:
                content = gh.raw(f"{cfg.get('tools_path', 'tools')}/{t}")
                if content:
                    with open(os.path.join(WORK_DIR, t), 'wb') as f:
                        f.write(content)
            tool_dir = WORK_DIR
            # 이 업데이트 프로그램 자신도 새 버전이 있으면 바꿔 둔다(다음 실행부터 적용)
            me = os.path.abspath(__file__)
            new_me = gh.raw(f"{cfg.get('tools_path', 'tools')}/update_gui.py")
            with open(me, 'rb') as f:
                cur = f.read()
            if new_me and new_me.replace(b'\r\n', b'\n') != cur.replace(b'\r\n', b'\n'):
                with open(me, 'wb') as f:
                    f.write(new_me)
                raise StepError('업데이트 프로그램이 새 버전으로 바뀌었습니다. 이 창을 닫고 규정업데이트.bat을 다시 실행해 주세요.')
            log('  완료')
        except StepError as e:
            if '새 버전' in str(e):
                raise
            log(f'  받지 못해 이 폴더의 프로그램을 씁니다: {e}')
    for t in TOOLS:
        if not os.path.exists(os.path.join(tool_dir, t)):
            raise StepError(f'{t} 파일이 없습니다. update_gui.py와 같은 폴더에 두거나 "최신 프로그램 받기"를 켜 주세요.')

    # 1) 현재 저장소 데이터
    log('■ 저장소의 현재 데이터를 확인하는 중…')
    rules_sha, old_rules_raw = gh.read('rules.json')
    laws_sha, old_laws_raw = gh.read('laws.json')
    changes_sha, old_changes_raw = gh.read('changes.json')
    old_rules = json.loads(old_rules_raw) if old_rules_raw else None
    old_laws = json.loads(old_laws_raw) if old_laws_raw else None
    log(f"  현재 규정 {len(old_rules['rules']) if old_rules else 0}건, 법령 확인 {len(old_laws['laws']) if old_laws else 0}건")

    # 2) 규정 수집
    log('■ 서원대학교 규정집에서 규정을 수집하는 중… (5분 정도 걸립니다)')
    rules_path = os.path.join(WORK_DIR, 'rules.json')
    if os.path.exists(rules_path):
        os.remove(rules_path)
    run_tool([os.path.join(tool_dir, 'seowon_rules_crawler.py'), '--out', rules_path] + list(cfg.get('crawler_args') or []), log, stop)
    with open(rules_path, encoding='utf-8') as f:
        new_rules = json.load(f)
    n_old, n_new = len(old_rules['rules']) if old_rules else 0, len(new_rules['rules'])
    if old_rules and n_new < n_old * MIN_RATIO:
        raise StepError(f'수집된 규정이 {n_new}건으로 기존 {n_old}건보다 크게 적어 업로드하지 않았습니다. '
                        '학교 사이트 접속 상태를 확인한 뒤 다시 시도해 주세요.')
    # 본문을 못 읽은 규정(HWPX 전환 등)은 규정 수에 잡혀도 본문이 제목뿐이다 → check_data.js와 같은 기준으로 막는다
    thin = [r['title'] for r in new_rules['rules'] if len(r.get('text') or '') < 200]
    if n_new and len(thin) > n_new * MAX_THIN_RATIO:
        raise StepError(f'본문을 읽지 못한 규정이 {len(thin)}건(전체 {n_new}건)이라 업로드하지 않았습니다: '
                        + ', '.join(thin[:10]) + (' 외' if len(thin) > 10 else '')
                        + '. 학교가 파일 형식(HWPX·PDF)을 바꿨을 수 있습니다. Claude에게 확인을 요청해 주세요.')
    if thin:
        log(f'  ! 본문이 거의 없는 규정 {len(thin)}건: ' + ', '.join(thin))
    added, removed, changed = rules_summary(old_rules, new_rules)

    # 3) 법령 확인
    new_laws = None
    law_event = None
    if cfg.get('check_laws'):
        log('■ 국가법령정보센터에서 인용 법령을 확인하는 중… (3분 정도 걸립니다)')
        laws_path = os.path.join(WORK_DIR, 'laws.json')
        topics_path = os.path.join(WORK_DIR, 'topics.json')
        try:  # 분야별 기본 법령 목록(저장소의 data/topics.json)
            _, topics_raw = gh.read('topics.json')
        except StepError:
            topics_raw = None
        if topics_raw:
            with open(topics_path, 'wb') as f:
                f.write(topics_raw)
        elif os.path.exists(topics_path):
            os.remove(topics_path)
        text_dir = os.path.join(WORK_DIR, 'lawtext')
        if os.path.isdir(text_dir):
            shutil.rmtree(text_dir)
        run_tool([os.path.join(tool_dir, 'law_check.py'), '--oc', cfg['oc'], '--rules', rules_path, '--out', laws_path,
                  '--topics', topics_path, '--text-dir', text_dir] + list(cfg.get('law_args') or []), log, stop)
        with open(laws_path, encoding='utf-8') as f:
            new_laws = json.load(f)

    # 4) 업로드 (바뀐 파일만)
    log('■ GitHub에 올리는 중…')
    today = dt.date.today().isoformat()
    uploaded = []
    if old_rules and same_rules(old_rules, new_rules):
        log('  규정: 바뀐 내용이 없어 올리지 않습니다.')
    else:
        with open(rules_path, 'rb') as f:
            gh.write('rules.json', f.read(), rules_sha,
                     f'규정 데이터 갱신 ({today}): 규정 {n_new}건, 추가 {len(added)}·삭제 {len(removed)}·변경 {len(changed)}')
        uploaded.append('rules.json')
        log('  규정 데이터를 올렸습니다.')
        # 개정 이력: 이전 수집본이 있을 때만(처음 올릴 때는 비교 대상이 없음)
        if old_rules and (added or removed or changed):
            history = json.loads(old_changes_raw) if old_changes_raw else {'events': []}
            event = change_event(old_rules, new_rules, new_rules.get('fetchedAt') or dt.datetime.now().astimezone().isoformat(timespec='seconds'))
            history['events'] = ([event] + history.get('events', []))[:MAX_EVENTS]
            gh.write('changes.json', json.dumps(history, ensure_ascii=False, indent=1).encode('utf-8'), changes_sha,
                     f'개정 이력 추가 ({today}): 추가 {len(added)}·삭제 {len(removed)}·변경 {len(changed)}')
            uploaded.append('changes.json')
            log('  개정 이력을 기록했습니다.')
    if new_laws is not None:
        # 법령 전체 조문(상위법 비교용): 바뀐 법령 파일만 커밋 하나로 올린다. laws.json보다 먼저 올려야 앱이 없는 파일을 찾지 않는다.
        old_map = (old_laws or {}).get('laws', {})
        texts = {}
        for k, v in new_laws['laws'].items():
            if v.get('text') and old_map.get(k, {}).get('textHash') != v.get('textHash'):
                with open(os.path.join(WORK_DIR, v['text'].replace('/', os.sep)), 'rb') as f:
                    texts[f"{cfg['data_path']}/{v['text']}"] = f.read()
        # 상위법 개정 알림: 올리기 전에 저장소의 이전 조문과 비교해 바뀐 조를 기록한다
        def old_text(o):
            try:
                _, raw = gh.read(o['text'])
                return json.loads(raw)['articles'] if raw else None
            except (StepError, ValueError, KeyError):
                return None

        def new_text(v):
            try:
                with open(os.path.join(WORK_DIR, v['text'].replace('/', os.sep)), encoding='utf-8') as f:
                    return json.load(f)['articles']
            except (OSError, ValueError, KeyError):
                return None
        law_event = law_change_event(old_laws, new_laws, old_text, new_text, new_laws.get('checkedAt') or today)
        if law_event:
            lc_sha, lc_raw = gh.read('lawchanges.json')
            hist = json.loads(lc_raw) if lc_raw else {'events': []}
            hist['events'] = ([law_event] + hist.get('events', []))[:MAX_EVENTS]
            names = ', '.join(x['name'] for x in law_event['laws'][:5]) + (f" 외 {len(law_event['laws']) - 5}개" if len(law_event['laws']) > 5 else '')
            texts[f"{cfg['data_path']}/lawchanges.json"] = json.dumps(hist, ensure_ascii=False, indent=1).encode('utf-8')
            log(f"  상위법 개정 {len(law_event['laws'])}개를 찾았습니다: {names}")
        if texts:
            n_text = len(texts) - (1 if law_event else 0)
            gh.commit_files(texts, f'법령 전체 조문 갱신 ({today}): {n_text}개' + (f", 상위법 개정 {len(law_event['laws'])}개" if law_event else ''))
            uploaded.append(f'법령 조문 {n_text}개' + (' + 상위법 개정 알림' if law_event else ''))
            log(f'  법령 전체 조문 {n_text}개를 올렸습니다.')
        if old_laws and same_laws(old_laws, new_laws) and 'rules.json' not in uploaded:
            log('  법령 확인: 바뀐 내용이 없어 올리지 않습니다.')
        else:
            with open(os.path.join(WORK_DIR, 'laws.json'), 'rb') as f:
                gh.write('laws.json', f.read(), laws_sha, f"법령 확인 결과 갱신 ({today}): 법령 {len(new_laws['laws'])}개")
            uploaded.append('laws.json')
            log('  법령 확인 결과를 올렸습니다.')

    lines = [f'규정 {n_new}건 (추가 {len(added)}, 삭제 {len(removed)}, 변경 {len(changed)})']
    for label, items in (('추가', added), ('삭제', removed), ('변경', changed)):
        if items:
            lines.append(f'  {label}: ' + ', '.join(items[:10]) + (f' 외 {len(items) - 10}건' if len(items) > 10 else ''))
    if new_laws is not None:
        nf = sum(1 for v in new_laws['laws'].values() if not v['found'])
        lines.append(f"법령 {len(new_laws['laws'])}개 확인 (찾지 못한 법령 {nf}개)")
        if law_event:
            lines.append('상위법 개정: ' + ', '.join(f"{x['name']}({len(x['articles'])}개 조)" for x in law_event['laws']))
    lines.append('업로드: ' + (', '.join(uploaded) if uploaded else '바뀐 내용 없음'))
    if uploaded:
        lines.append('1~2분 뒤 앱을 열면 새 데이터로 바뀝니다.')
    return '\n'.join(lines)


# ---------------- 화면 ----------------

def main():
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    cfg = load_config()
    root = tk.Tk()
    root.title('서원대학교 규정집 업데이트')
    root.geometry('720x560')
    root.minsize(560, 420)

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill='both', expand=True)
    frm.columnconfigure(1, weight=1)

    ttk.Label(frm, text='서원대학교 규정집 데이터 업데이트', font=('Malgun Gothic', 14, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w')
    ttk.Label(frm, text='규정 수집 → 국가법령정보센터 법령 확인 → GitHub 업로드를 한 번에 실행합니다.').grid(
        row=1, column=0, columnspan=3, sticky='w', pady=(2, 10))

    oc_var = tk.StringVar(value=cfg['oc'])
    token_var = tk.StringVar(value=cfg['token'])
    laws_var = tk.BooleanVar(value=cfg['check_laws'])
    tools_var = tk.BooleanVar(value=cfg['update_tools'])
    repo_var = tk.StringVar(value=cfg.get('repo', ''))

    ttk.Label(frm, text='법령 API 인증키(OC)').grid(row=2, column=0, sticky='w')
    ttk.Entry(frm, textvariable=oc_var).grid(row=2, column=1, columnspan=2, sticky='ew', pady=2)
    ttk.Label(frm, text='GitHub 토큰').grid(row=3, column=0, sticky='w')
    tok = ttk.Entry(frm, textvariable=token_var, show='•')
    tok.grid(row=3, column=1, sticky='ew', pady=2)
    ttk.Button(frm, text='보기', width=6, command=lambda: tok.config(show='' if tok.cget('show') else '•')).grid(row=3, column=2, padx=(4, 0))
    ttk.Label(frm, text='GitHub 저장소').grid(row=4, column=0, sticky='w')
    ttk.Entry(frm, textvariable=repo_var).grid(row=4, column=1, columnspan=2, sticky='ew', pady=2)
    opts = ttk.Frame(frm)
    opts.grid(row=5, column=0, columnspan=3, sticky='w', pady=(4, 8))
    ttk.Checkbutton(opts, text='국가법령정보센터 법령 확인 포함', variable=laws_var).pack(side='left')
    ttk.Checkbutton(opts, text='실행 전 최신 프로그램 받기', variable=tools_var).pack(side='left', padx=(16, 0))

    btns = ttk.Frame(frm)
    btns.grid(row=6, column=0, columnspan=3, sticky='ew')
    start_btn = ttk.Button(btns, text='업데이트 시작')
    start_btn.pack(side='left')
    stop_btn = ttk.Button(btns, text='중지', state='disabled')
    stop_btn.pack(side='left', padx=6)
    status = ttk.Label(btns, text='준비')
    status.pack(side='left', padx=10)

    logbox = scrolledtext.ScrolledText(frm, height=18, font=('Consolas', 9), state='disabled')
    logbox.grid(row=7, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
    frm.rowconfigure(7, weight=1)

    q = queue.Queue()
    stop = threading.Event()

    def log(line):
        q.put(('log', line))

    def pump():
        try:
            while True:
                kind, val = q.get_nowait()
                if kind == 'log':
                    logbox.config(state='normal')
                    logbox.insert('end', val + '\n')
                    logbox.see('end')
                    logbox.config(state='disabled')
                elif kind == 'done':
                    start_btn.config(state='normal')
                    stop_btn.config(state='disabled')
                    ok, msg = val
                    status.config(text='완료' if ok else '실패')
                    (messagebox.showinfo if ok else messagebox.showerror)('업데이트 ' + ('완료' if ok else '실패'), msg)
        except queue.Empty:
            pass
        root.after(150, pump)

    def worker(c):
        try:
            summary = update(c, log, stop)
            log('\n' + summary)
            q.put(('done', (True, summary)))
        except StepError as e:
            log(f'\n✗ {e}')
            q.put(('done', (False, str(e))))
        except Exception as e:  # 예상하지 못한 오류도 화면에 보여준다
            log(f'\n✗ 예상하지 못한 오류: {e!r}')
            q.put(('done', (False, f'예상하지 못한 오류: {e!r}')))

    def start():
        c = dict(cfg, oc=oc_var.get().strip(), token=token_var.get().strip(), repo=repo_var.get().strip(),
                 check_laws=laws_var.get(), update_tools=tools_var.get())
        save_config(c)
        cfg.update(c)
        logbox.config(state='normal')
        logbox.delete('1.0', 'end')
        logbox.config(state='disabled')
        stop.clear()
        start_btn.config(state='disabled')
        stop_btn.config(state='normal')
        status.config(text='실행 중…')
        threading.Thread(target=worker, args=(c,), daemon=True).start()

    start_btn.config(command=start)
    stop_btn.config(command=lambda: (stop.set(), status.config(text='중지하는 중…')))
    log(f'설정 저장 위치: {CONFIG_PATH}')
    pump()
    root.mainloop()


if __name__ == '__main__':
    if '--cli' in sys.argv:
        # 화면 없이 실행(시험·작업 스케줄러용): 저장된 설정을 사용
        try:
            print(update(load_config(), print))
        except StepError as e:
            sys.exit(f'✗ {e}')
    else:
        main()
