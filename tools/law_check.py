#!/usr/bin/env python3
"""규정에서 인용한 국가 법령·행정규칙을 국가법령정보센터(law.go.kr) Open API로 확인한다.

규정집(data/rules.json)에서 「고등교육법」 제6조, 「대학교원 자격기준 등에 관한 규정」,
교육공무원법 제25조처럼 인용한 법률·시행령·시행규칙·고시·훈령·예규를 찾아
  - 그 이름의 현행 법령이 있는지(폐지·제명 변경 여부)
  - 인용한 조(제N조, 제N조의M)가 있는지, 삭제된 조는 아닌지
를 확인하고, 인용한 조문의 현행 내용과 함께 data/laws.json 으로 저장한다.
모바일 앱은 이 파일을 읽어 정합성 점검에 반영하고 인용 조문을 바로 보여준다.

사용법
    pip install requests
    python law_check.py --oc 내OC --rules rules.json --out laws.json
    python law_check.py --oc 내OC --debug 고등교육법          # API 응답 확인용
    python law_check.py --seed 다른/laws.json --text-dir ../data/lawtext   # 인증키 없이: 이미 받아 둔 국가법령정보센터 자료로 확인

OC(인증키)는 국가법령정보 공동활용(https://open.law.go.kr)에서 OPEN API를 신청할 때
직접 정하는 값이다. 신청 화면에서 '서버장비의 IP주소'에 이 프로그램을 실행할 PC의 공인 IP를
등록하고, 공동활용 법령종류는 '대한민국 현행법령'과 '현행 행정규칙'의 목록 JSON·본문 JSON을 고른다.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:  # pragma: no cover
    sys.exit('필요한 패키지가 없습니다: pip install requests')

API = 'https://www.law.go.kr/DRF'
VIEW = 'https://www.law.go.kr'

# 인용 이름 끝말로 국가 법령·행정규칙 후보를 고른다.
LAW_SUFFIX = r'(?:법|법률|령|규칙|고시|훈령|예규|규정)'
BRACKET_RE = re.compile(r'[「『]([^」』\n]{2,80})[」』]\s*(?:의\s*)?(?:제\s*(\d+)\s*조(?:\s*의\s*(\d+))?)?')
# 괄호 없이 쓴 "고등교육법 제6조", "고등교육법 시행령 제14조"
PLAIN_RE = re.compile(r'(?<![가-힣·ㆍ])([가-힣][가-힣·ㆍ]{1,30}(?:법|법률)(?:\s*시행(?:령|규칙))?)\s*(?:의\s*)?제\s*(\d+)\s*조(?:\s*의\s*(\d+))?')
NOT_LAW_WORDS = {'방법', '불법', '용법', '문법', '수법', '기법', '편법', '위법', '적법', '합법', '법', '사법', '해법', '공법', '사용법', '작성법'}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def norm(s):
    return re.sub(r'[\s「」『』"\'“”‘’<>〈〉《》·ㆍ・․‧,]', '', s or '')


def rule_keys(title):
    """앱(engine.js)과 같은 방식: 공백 등을 없애고 '서원대학교/학교법인/서원학원' 앞말을 뺀 이름도 같은 규정으로 본다."""
    n = norm(title)
    return {n, re.sub(r'^(?:서원대학교|학교법인|서원학원|본대학교|본교|본)+', '', n)}


def collect_citations(rules):
    """규정 본문에서 국가 법령 인용 후보를 모은다. {이름: {'bracket': bool, 'articles': {key}, 'from': [...]}}"""
    internal = set()
    for r in rules:
        internal |= rule_keys(r['title'])
    cites = {}

    def add(name, art, sub, where, bracket):
        name = re.sub(r'\s+', ' ', name).strip()
        name = re.sub(r'\s*제\s*\d+\s*조.*$', '', name)
        name = re.split(r'\s+및\s+(?:동법\s*)?시행', name)[0]  # "○○법률 및 시행령, 시행규칙"
        n = norm(name)
        if len(n) < 3 or n in NOT_LAW_WORDS or rule_keys(name) & internal or not re.search(LAW_SUFFIX + '$', n):
            return
        c = cites.setdefault(n, {'name': name, 'bracket': False, 'articles': set(), 'from': []})
        c['bracket'] |= bracket
        if art:
            c['articles'].add(f'{int(art)}-{int(sub)}' if sub else str(int(art)))
        if len(c['from']) < 5 and where not in c['from']:
            c['from'].append(where)

    for r in rules:
        text = r.get('text') or '\n'.join(a.get('text', '') for a in r.get('articles', []))
        for m in BRACKET_RE.finditer(text):
            add(m.group(1), m.group(2), m.group(3), r['title'], True)
        for m in PLAIN_RE.finditer(text):
            add(m.group(1), m.group(2), m.group(3), r['title'], False)
    return cites


class LawApi:
    def __init__(self, oc, delay, base=API):
        self.base = base.rstrip('/')
        self.oc = oc
        self.delay = delay
        self.s = requests.Session()
        self.s.headers['User-Agent'] = 'Mozilla/5.0 (seowon-rules law check)'
        self._last = 0.0

    def get(self, path, **params):
        params.update(OC=self.oc, type='JSON')
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                r = self.s.get(f'{self.base}/{path}', params=params, timeout=30)
                self._last = time.time()
                r.raise_for_status()
                txt = r.text.strip()
                if not txt.startswith(('{', '[')):
                    # 인증 실패 등은 HTML/텍스트로 온다
                    raise RuntimeError(re.sub(r'<[^>]+>|\s+', ' ', txt)[:200])
                return r.json()
            except (requests.RequestException, ValueError) as e:
                if attempt == 2:
                    raise RuntimeError(f'API 요청 실패: {e}')
                time.sleep(2 ** attempt)


class PartialArticles(dict):
    """전체 조문이 아니라 일부 조문만 있는 경우"""
    partial = True


class SeedApi:
    """국가법령정보센터 Open API 대신, 이미 그 API로 받아 둔 laws.json·lawtext/를 읽는다.
    인증키(OC)·IP 등록 없이 확인할 때 쓴다(예: 다른 학교 규정집 앱이 받아 둔 같은 법령 자료)."""
    offline = True

    def __init__(self, seed_path):
        with open(seed_path, encoding='utf-8') as f:
            self.data = json.load(f)
        self.laws = {k: v for k, v in self.data.get('laws', {}).items() if v.get('found')}
        self.dir = os.path.dirname(os.path.abspath(seed_path))
        self.checked = self.data.get('checkedAt', '')
        self.unknown = []

    def search(self, name):
        v = self.laws.get(norm(name))
        if not v:
            return []
        return [{'target': 'seed', 'name': v['name'], 'id': norm(name), 'type': v.get('type', ''),
                 'current': v.get('current', True), 'enforced': v.get('enforced', ''),
                 'promulgated': v.get('promulgated', ''), 'url': v.get('url', '')}]

    def articles(self, hit):
        v = self.laws[hit['id']]
        if v.get('text'):
            p = os.path.join(self.dir, v['text'])
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    return dict(json.load(f)['articles'])
        return PartialArticles({k: a for k, a in (v.get('articles') or {}).items() if not a.get('missing')})


def walk(obj):
    """JSON 안의 모든 dict를 차례로 돌려준다(응답 구조가 조금 달라도 찾을 수 있게)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def first(d, *keys):
    for k in keys:
        if d.get(k) not in (None, ''):
            return str(d[k]).strip()
    return ''


def _search_once(api, query):
    out = []
    for target, name_keys, id_keys in (
            ('law', ('법령명한글', '법령명'), ('법령일련번호', 'MST')),
            ('admrul', ('행정규칙명',), ('행정규칙일련번호', '행정규칙ID'))):
        data = api.get('lawSearch.do', target=target, query=query, display=100)
        for d in walk(data):
            nm = first(d, *name_keys)
            if not nm:
                continue
            out.append({
                'target': target,
                'name': nm,
                'id': first(d, *id_keys),
                'type': first(d, '법령구분명', '행정규칙종류', '법령종류'),
                'current': first(d, '현행연혁코드', '현행연혁구분') in ('', '현행'),
                'enforced': first(d, '시행일자'),
                'promulgated': first(d, '공포일자'),
            })
        if any(norm(o['name']) == norm(query) for o in out):
            break
    return out


def search(api, name):
    if getattr(api, 'offline', False):
        return api.search(name)
    """법령(법률·시행령·시행규칙)과 행정규칙(고시·훈령·예규)에서 이름으로 찾는다.
    "원자력안전법시행규칙"처럼 띄어 쓰지 않은 이름은 모법 이름으로 다시 찾아 맞춘다.
    반환: [{'target', 'name', 'id', 'type', 'current', 'enforced'}]"""
    out = _search_once(api, name)
    # 띄어쓰기·가운뎃점이 다르게 쓰인 경우("대학설립 운영규정" ↔ "대학설립·운영 규정")를 위해 짧은 검색어로 다시 찾는다.
    retries = [re.sub(r'\s*시행\s*(령|규칙)$', '', name), re.split(r'[\s·ㆍ]', name)[0]]
    for q in retries:
        if any(norm(o['name']) == norm(name) for o in out):
            break
        if q != name and len(q) >= 2:
            out += _search_once(api, q)
    return out


ART_HEAD_RE = re.compile(r'^\s*제\s*(\d+)\s*조(?:\s*의\s*(\d+))?\s*(?:\(([^)]*)\))?')


def law_articles(api, hit):
    """{조키: {'no', 'title', 'text', 'deleted'}}"""
    if getattr(api, 'offline', False):
        return api.articles(hit)
    arts = {}
    if hit['target'] == 'law':
        data = api.get('lawService.do', target='law', MST=hit['id'])
        for d in walk(data):
            if '조문번호' not in d:
                continue
            if first(d, '조문여부') not in ('', '조문'):
                continue  # 장·절 제목 등
            n, sub = first(d, '조문번호'), first(d, '조문가지번호')
            key = f'{int(n)}-{int(sub)}' if sub and sub != '0' else str(int(n))
            parts = [first(d, '조문내용')]
            for h in walk(d.get('항', [])):
                for k in ('항내용', '호내용', '목내용'):
                    if h.get(k):
                        parts.append(str(h[k]).strip())
            text = '\n'.join(p for p in parts if p)
            arts[key] = {
                'no': f'제{int(n)}조' + (f'의{int(sub)}' if key.find('-') > 0 else ''),
                'title': first(d, '조문제목'),
                'text': text,
                'deleted': bool(re.match(r'^\s*제\s*\d+\s*조(?:의\s*\d+)?\s*(?:\([^)]*\))?\s*삭제', text)),
            }
    else:
        data = api.get('lawService.do', target='admrul', ID=hit['id'])
        lines = []
        for d in walk(data):
            v = d.get('조문내용')
            if isinstance(v, list):
                lines += [str(x) for x in v]
            elif isinstance(v, str):
                lines += v.split('\n')
        cur = None
        for l in lines:
            m = ART_HEAD_RE.match(l)
            if m:
                key = f'{int(m.group(1))}-{int(m.group(2))}' if m.group(2) else str(int(m.group(1)))
                cur = arts[key] = {'no': f'제{int(m.group(1))}조' + (f'의{int(m.group(2))}' if m.group(2) else ''),
                                   'title': (m.group(3) or '').strip(), 'text': l.strip(), 'deleted': False}
                cur['deleted'] = bool(re.search(r'^\s*제\s*\d+\s*조(?:의\s*\d+)?\s*(?:\([^)]*\))?\s*삭제', l))
            elif cur:
                cur['text'] += '\n' + l.strip()
    return arts


def view_url(hit):
    if hit.get('url'):
        return hit['url']
    return f"{VIEW}/{'법령' if hit['target'] == 'law' else '행정규칙'}/{hit['name'].replace(' ', '')}"


def add_area_laws(cites, path):
    """분야별 기본 법령(topics.json의 areas)도 확인 대상에 넣는다. 규정에서 인용하지 않은 법령이어도
    상위법 비교에서 전체 조문과 대조할 수 있게 한다. 반환: 새로 더한 법령 수"""
    if not path or not os.path.exists(path):
        return 0
    with open(path, encoding='utf-8') as f:
        areas = json.load(f).get('areas', [])
    n = 0
    for area in areas:
        for name in area.get('laws', []):
            if norm(name) not in cites:
                n += 1
            c = cites.setdefault(norm(name), {'name': name, 'bracket': True, 'articles': set(), 'from': []})
            c['bracket'] = True
    return n


def check(api, cites, full=False):
    """full이면 찾은 법령마다 전체 조문을 받아 entry['_all']에 둔다(저장할 때 따로 뺀다)."""
    results = {}
    total = len(cites)
    for i, (n, c) in enumerate(sorted(cites.items(), key=lambda x: (not x[1]['bracket'], x[0])), 1):
        try:
            hits = search(api, c['name'])
        except RuntimeError as e:
            sys.exit(f'국가법령정보센터 API 오류: {e}\nOC 값과 OPEN API 신청 시 등록한 IP를 확인해 주세요.')
        exact = [h for h in hits if norm(h['name']) == n]
        cur = [h for h in exact if h['current']] or exact
        if not cur and getattr(api, 'offline', False):
            if c['bracket']:
                api.unknown.append(c['name'])
            continue  # 받아 둔 자료에 없는 법령은 '없음'으로 단정하지 않고 건너뛴다(인증키로 다시 확인)
        if not cur:
            if not c['bracket']:
                continue  # 괄호 없이 쓴 낱말("○○법")은 법령으로 확인될 때만 남긴다
            similar = []
            for h in hits:
                if h['name'] not in similar and h['current']:
                    similar.append(h['name'])
            results[n] = {'name': c['name'], 'found': False, 'similar': similar[:5], 'from': c['from'],
                          'articles': sorted(c['articles'])}
            log(f'  [{i}/{total}] ✗ {c["name"]}' + (f'  (비슷한 현행 법령: {", ".join(similar[:3])})' if similar else ''))
            continue
        hit = cur[0]
        entry = {'name': hit['name'], 'found': True, 'type': hit['type'], 'current': hit['current'],
                 'enforced': hit['enforced'], 'promulgated': hit.get('promulgated', ''), 'url': view_url(hit), 'from': c['from'], 'articles': {}}
        if c['articles'] or full:
            try:
                arts = law_articles(api, hit)
            except RuntimeError as e:
                log(f'  ! {hit["name"]} 조문을 받지 못함: {e}')
                arts = None
            if full and arts and not getattr(arts, 'partial', False):
                entry['_all'] = arts
            for key in sorted(c['articles'], key=lambda k: [int(x) for x in k.split('-')]):
                if arts is None:
                    continue
                a = arts.get(key)
                if not a and getattr(arts, 'partial', False):
                    continue  # 일부 조문만 받아 둔 법령: 없는 조라고 단정하지 않는다
                entry['articles'][key] = a if a else {'missing': True}
        results[n] = entry
        miss = [k for k, a in entry['articles'].items() if a.get('missing')]
        dele = [k for k, a in entry['articles'].items() if a.get('deleted')]
        log(f'  [{i}/{total}] ✓ {hit["name"]} ({hit["type"]}{"" if hit["current"] else ", 현행 아님"})'
            + (f'  없는 조: {", ".join(miss)}' if miss else '') + (f'  삭제된 조: {", ".join(dele)}' if dele else ''))
    return results


def extra_articles(rules, results):
    """확인된 법령 이름이 괄호 없이 쓰인 곳("교원자격검정령 제17조", 띄어쓰기가 다른 이름 포함)에서
    인용한 조를 더 모은다. 앱(engine.js)도 확인된 이름으로 같은 방식으로 인용을 찾는다.
    반환: {법령 키: {조 키}}"""
    names = sorted(((k, v) for k, v in results.items() if v['found'] and len(k) >= 3), key=lambda x: -len(x[0]))
    found = {}
    for r in rules:
        text = r.get('text') or ''
        chars = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
        compact = ''.join(ch for _, ch in chars)
        for k, v in names:
            start = 0
            while True:
                idx = compact.find(k, start)
                if idx < 0:
                    break
                start = idx + len(k)
                end = chars[idx + len(k) - 1][0] + 1
                m = re.match(r'\s*(?:의\s*)?제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', text[end:end + 20])
                if m:
                    key = f'{int(m.group(1))}-{int(m.group(2))}' if m.group(2) else str(int(m.group(1)))
                    if key not in v['articles']:
                        found.setdefault(k, set()).add(key)
    return found


def text_file_id(key):
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]


def save_texts(results, text_dir):
    """법령마다 전체 조문을 text_dir/<id>.json으로 저장하고 laws.json 항목에 파일 경로와 해시를 적는다.
    앱은 상위법 비교에서 검증할 때만 이 파일을 받는다."""
    os.makedirs(text_dir, exist_ok=True)
    n = 0
    for k, v in results.items():
        arts = v.pop('_all', None)
        if not arts:
            continue
        body = json.dumps({'name': v['name'], 'url': v.get('url', ''), 'articles': arts},
                          ensure_ascii=False, indent=0, sort_keys=True).encode('utf-8')
        fid = text_file_id(k)
        with open(os.path.join(text_dir, fid + '.json'), 'wb') as f:
            f.write(body)
        v['text'] = f'lawtext/{fid}.json'
        v['textHash'] = hashlib.sha1(body).hexdigest()
        v['articleCount'] = len(arts)
        n += 1
    log(f'법령 전체 조문 {n}개를 {os.path.abspath(text_dir)} 에 저장했습니다.')


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='규정이 인용한 국가 법령을 국가법령정보센터 Open API로 확인')
    ap.add_argument('--oc', default=os.environ.get('LAW_OC'), help='국가법령정보 Open API 인증키(OC). 환경변수 LAW_OC로도 지정 가능')
    ap.add_argument('--rules', default=os.path.join(here, '..', 'data', 'rules.json'), help='규정 데이터 (기본: data/rules.json)')
    ap.add_argument('--out', default=os.path.join(here, '..', 'data', 'laws.json'), help='저장할 파일 (기본: data/laws.json)')
    ap.add_argument('--topics', default=os.path.join(here, '..', 'data', 'topics.json'), help='분야별 기본 법령 목록 (기본: data/topics.json, 없으면 건너뜀)')
    ap.add_argument('--text-dir', help='찾은 법령의 전체 조문을 이 폴더에 법령마다 파일로 저장 (laws.json 기준 상대 경로 lawtext/로 기록)')
    ap.add_argument('--delay', type=float, default=0.3, help='요청 간 대기(초)')
    ap.add_argument('--api', default=API, help=argparse.SUPPRESS)  # 시험용 서버 주소
    ap.add_argument('--debug', metavar='법령명', help='한 법령의 검색·조문 API 응답을 그대로 출력(문제 확인용)')
    ap.add_argument('--seed', metavar='laws.json', help='API 대신 이미 국가법령정보센터에서 받아 둔 laws.json(+lawtext/)으로 확인. 그 자료에 없는 법령은 건너뜀')
    args = ap.parse_args()
    if args.seed:
        api = SeedApi(args.seed)
    elif not args.oc:
        sys.exit('OC(인증키)가 필요합니다: --oc 내OC  (https://open.law.go.kr 에서 OPEN API 신청)')
    else:
        api = LawApi(args.oc, args.delay, args.api)

    if args.debug and not args.seed:
        data = api.get('lawSearch.do', target='law', query=args.debug, display=5)
        print('--- lawSearch(law) ---')
        print(json.dumps(data, ensure_ascii=False, indent=1)[:3000])
        hits = search(api, args.debug)
        print('--- 해석한 검색 결과 ---')
        for h in hits[:10]:
            print(h)
        exact = [h for h in hits if norm(h['name']) == norm(args.debug)]
        if exact:
            arts = law_articles(api, exact[0])
            print(f'--- 조문 {len(arts)}개 중 앞부분 ---')
            for k in list(arts)[:5]:
                print(k, arts[k]['no'], arts[k]['title'], '|', arts[k]['text'][:80].replace('\n', ' '))
        return

    with open(args.rules, encoding='utf-8') as f:
        rules = json.load(f)['rules']
    cites = collect_citations(rules)
    log(f'규정 {len(rules)}건에서 국가 법령 인용 후보 {len(cites)}개를 찾았습니다.')
    nt = add_area_laws(cites, args.topics)
    if nt:
        log(f'분야별 기본 법령 {nt}개를 더합니다.')
    log('받아 둔 국가법령정보센터 자료로 확인합니다.' if args.seed else '국가법령정보센터에서 확인합니다.')
    results = check(api, cites, full=bool(args.text_dir))
    more = extra_articles(rules, results)
    if more:
        log(f'괄호 없이 인용한 조문 {sum(len(v) for v in more.values())}개를 더 확인합니다.')
        for k, keys in more.items():
            v = results[k]
            arts = v.get('_all')
            if arts is None:
                hits = [h for h in search(api, v['name']) if norm(h['name']) == norm(v['name'])]
                hit = ([h for h in hits if h['current']] or hits or [None])[0]
                if not hit:
                    continue
                arts = law_articles(api, hit)
            for key in keys:
                v['articles'][key] = arts.get(key) or {'missing': True}
    if args.text_dir:
        save_texts(results, args.text_dir)
    data = {
        'source': '국가법령정보센터 Open API (law.go.kr)',
        'checkedAt': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
        'laws': results,
    }
    if args.seed:
        data['source'] += f' — 받아 둔 자료({api.checked or "날짜 미상"} 기준)'
        data['lawDataAt'] = api.checked
        data['unchecked'] = sorted(set(api.unknown))
        if api.unknown:
            log(f'받아 둔 자료에 없어 확인하지 못한 법령 {len(set(api.unknown))}개(인증키로 다시 확인 필요): ' + ', '.join(sorted(set(api.unknown))[:30]))
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    nf = sum(1 for v in results.values() if not v['found'])
    na = sum(1 for v in results.values() if v['found'] for a in v['articles'].values() if a.get('missing'))
    log(f'완료: 법령 {len(results)}개 확인 (없는 법령 {nf}개, 없는 조 {na}개) → {os.path.abspath(args.out)}')


if __name__ == '__main__':
    main()
