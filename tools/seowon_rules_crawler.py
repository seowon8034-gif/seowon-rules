#!/usr/bin/env python3
"""서원대학교 규정집 수집기.

서원대학교 홈페이지 규정집(https://www.seowon.ac.kr/seowon/301/subview.do)에서
  1) 분류 트리(get_node)를 따라 편·장과 규정 목록을 모으고
  2) 규정마다 제·개정 이력(registView)에서 가장 최근 판을 고른 뒤
  3) 현행 규정 HWP 파일(download.do)을 받아 본문을 텍스트로 뽑아
rules.json(앱이 읽는 형식)으로 저장합니다.

  python seowon_rules_crawler.py                       # → ../data/rules.json
  python seowon_rules_crawler.py --out rules.json --history-days 120 --changes ../data/changes.json

필요: pip install olefile
"""
import argparse
import datetime as dt
import html as htmlmod
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from hwp5text import hwp_to_text  # noqa: E402

BASE = 'https://www.seowon.ac.kr'
SITE, FNCT, ROOT = 'seowon', '7', '3'
LIST_PAGE = BASE + '/seowon/301/subview.do'
UA = 'Mozilla/5.0 (seowon-rules-crawler)'
KST = dt.timezone(dt.timedelta(hours=9))


def log(*a):
    print(*a, file=sys.stderr, flush=True)


class Client:
    def __init__(self, delay=0.4, timeout=40):
        self.delay, self.timeout, self._last = delay, timeout, 0.0

    def _wait(self):
        d = time.time() - self._last
        if d < self.delay:
            time.sleep(self.delay - d)
        self._last = time.time()

    def fetch(self, url, data=None, ajax=False, tries=4):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        headers = {'User-Agent': UA, 'Referer': LIST_PAGE}
        if ajax:
            headers['X-Requested-With'] = 'XMLHttpRequest'
        for i in range(tries):
            self._wait()
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except Exception as e:  # 네트워크 오류는 잠시 쉬고 다시
                if i == tries - 1:
                    raise
                log(f'  다시 시도({i + 1}): {url} — {e}')
                time.sleep(2 ** i)

    def text(self, url, data=None, ajax=False):
        return self.fetch(url, data, ajax).decode('utf-8', 'replace')


# ---------------- 목록 ----------------

def get_nodes(c, node_id):
    url = f'{BASE}/regltn/{SITE}/{FNCT}/get_node/operation?id={node_id}'
    return json.loads(c.text(url, ajax=True))


def walk_tree(c):
    """[(node_id, 제목, 상위노드, 편, 장, 자식있음)]"""
    out = []

    def walk(nid, part, chapter):
        for n in get_nodes(c, nid):
            text = re.sub(r'\s+', ' ', n['text']).strip()
            is_part = re.match(r'^제\s*\d+\s*편', text)
            is_chap = re.match(r'^제\s*\d+\s*장', text)
            if is_part:
                walk(n['id'], text, '')
            elif is_chap:
                walk(n['id'], part, text)
            else:
                out.append({'node': n['id'], 'title': text, 'parent': nid, 'part': part, 'chapter': chapter})
                if n.get('children'):
                    walk(n['id'], part, chapter)
    walk(ROOT, '', '')
    return out


ITEM_RE = re.compile(r"jf_regltn_view\('(\d+)'\);\">\s*([^<]*?)\s*</a>\s*</td>\s*<td>\s*([^<]*?)\s*</td>\s*"
                     r"(?:<td>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})?\s*</td>)?", re.S)


def _detail(block, label):
    m = re.search(r'<strong>' + label + r'</strong></th>\s*<td[^>]*>(.*?)</td>', block, re.S)
    if not m:
        return ''
    return htmlmod.unescape(re.sub(r'<[^>]+>', '', m.group(1))).strip()


def parse_list(html):
    """registView·view 목록 HTML → [{seq, title, code, date, dept, files:{1:이름,2:이름}, summary}]"""
    items = []
    for m in ITEM_RE.finditer(html):
        seq = m.group(1)
        start = html.find(f'id="regltnView_{seq}"', m.end())
        end = html.find('</table>', start) if start >= 0 else -1
        block = html[start:end] if start >= 0 else ''
        files = {}
        for fm in re.finditer(r'href="/regltn/[^"]*/' + seq + r'/(\d)/download\.do">([^<]+)</a>', block):
            files[fm.group(1)] = htmlmod.unescape(fm.group(2)).strip()
        if not files:
            for fm in re.finditer(r'/regltn/[^"]*/' + seq + r'/(\d)/download\.do', html[m.start():m.start() + 3000]):
                files.setdefault(fm.group(1), '')
        date = m.group(4) or _detail(block, r'제/개/폐정일')
        items.append({
            'seq': int(seq), 'title': htmlmod.unescape(m.group(2)).strip(), 'code': m.group(3).strip(),
            'date': date, 'dept': _detail(block, '소관부서'), 'summary': _detail(block, '주요내용'), 'files': files,
        })
    return items


def total_pages(html):
    m = re.search(r'_totPage">\s*(\d+)', html)
    return int(m.group(1)) if m else 1


def rule_versions(c, node, parent):
    url = f'{BASE}/regltn/{SITE}/{FNCT}/registView.do'
    html = c.text(url, {'id': node, 'parent': parent}, ajax=True)
    return parse_list(html)


def current_list(c):
    """규정집 첫 화면(현행 규정 목록) 전체 쪽."""
    url = f'{BASE}/regltn/{SITE}/{FNCT}/view.do'
    first = c.text(LIST_PAGE)
    items = parse_list(first)
    pages = total_pages(first)
    for p in range(2, pages + 1):
        items += parse_list(c.text(url, {'page': p}))
    return items


# ---------------- 본문 ----------------

def download_url(seq, kind=1):
    return f'{BASE}/regltn/{SITE}/{FNCT}/{seq}/{kind}/download.do'


def preview_url(seq, kind=1):
    return f'{BASE}/regltn/{SITE}/{FNCT}/{seq}/{kind}/synapView.do'


HEAD_RE = re.compile(r'^\s*\d+(?:-\d+)+\s*「[^」]+」\s*(?:\([^)]*\))?\s*$')


def clean_text(text, title):
    lines = []
    for raw in text.split('\n'):
        line = raw.rstrip()
        s = line.strip()
        if s in ('/', '／'):
            continue
        if HEAD_RE.match(s):  # "3-1-01 「직제 규정」 (개정 2026.10.28.)" 머리줄은 메타로 대신한다
            continue
        lines.append(line)
    out = '\n'.join(lines).strip()
    first = out.split('\n', 1)[0].strip()
    if re.sub(r'\s+', '', first) != re.sub(r'\s+', '', title):
        out = title + '\n\n' + out
    return re.sub(r'\n{3,}', '\n\n', out)


def fetch_text(c, seq, cache_dir=None):
    path = os.path.join(cache_dir, f'{seq}.hwp') if cache_dir else None
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, 'rb') as f:
            data = f.read()
    else:
        data = c.fetch(download_url(seq, 1))
        if path:
            with open(path, 'wb') as f:
                f.write(data)
    if not data.startswith(b'\xd0\xcf\x11\xe0'):
        raise ValueError('HWP 5.0 파일이 아닙니다(HWPX·PDF 등은 아직 지원하지 않음)')
    import io
    return hwp_to_text(io.BytesIO(data))


def fmt_date(d):
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', d or '')
    return f'{m.group(1)}.{m.group(2)}.{m.group(3)}' if m else ''


def code_category(code, parts):
    """규정번호 3-1-01 → ('제3편 행정', '제1장 일반')"""
    m = re.match(r'(\d+)-(\d+)', code or '')
    if not m:
        return '', ''
    p = next((x for x in parts if re.match(r'^제\s*' + m.group(1) + r'\s*편', x)), '')
    return p, ''


# ---------------- 실행 ----------------

def crawl(args):
    c = Client(delay=args.delay)
    log('분류 트리 읽는 중...')
    nodes = walk_tree(c)
    log(f'  규정 노드 {len(nodes)}개')
    log('현행 규정 목록 읽는 중...')
    cur = current_list(c)
    cur_by_seq = {x['seq']: x for x in cur}
    log(f'  현행 규정 {len(cur)}건')

    rules, seen_seq = [], set()
    history = {}
    for i, n in enumerate(nodes, 1):
        vers = rule_versions(c, n['node'], n['parent'])
        if not vers:
            log(f'  [{i}/{len(nodes)}] {n["title"]}: 판 없음(건너뜀)')
            continue
        vers.sort(key=lambda v: (v['date'], v['seq']), reverse=True)
        top = vers[0]
        # 현행 목록에 있는 판을 우선한다
        listed = [v for v in vers if v['seq'] in cur_by_seq]
        if listed:
            top = listed[0]
        history[n['node']] = vers
        seen_seq.add(top['seq'])
        rules.append({'node': n['node'], 'meta': top, 'part': n['part'], 'chapter': n['chapter'],
                      'treeTitle': n['title'], 'versions': vers})
        if i % 20 == 0:
            log(f'  이력 {i}/{len(nodes)}')

    # 첫 화면 목록에는 폐지·명칭 변경된 옛 규정도 섞여 있다. 분류 트리에 있는 규정을 현행 규정집으로 본다.
    extra = [x for x in cur if x['seq'] not in seen_seq]
    if extra:
        log(f'  분류 트리에 없는 목록 항목 {len(extra)}건(폐지·옛 명칭 등)은 '
            + ('함께 담습니다(--include-untree).' if args.include_untree else '뺍니다.'))
    if args.include_untree:
        parts = sorted({n['part'] for n in nodes if n['part']})
        for x in extra:
            p, ch = code_category(x['code'], parts)
            rules.append({'node': None, 'meta': x, 'part': p, 'chapter': ch, 'treeTitle': x['title'], 'versions': [x]})

    cache = args.cache
    if cache:
        os.makedirs(cache, exist_ok=True)
    out_rules, failed = [], []
    for i, r in enumerate(rules, 1):
        m = r['meta']
        title = m['title'] or r['treeTitle']
        try:
            text = clean_text(fetch_text(c, m['seq'], cache), title)
        except Exception as e:
            failed.append(title)
            log(f'  [{i}/{len(rules)}] {title}: 본문 실패 — {e}')
            text = title
        attachments = []
        names = m.get('files') or {}
        if '1' in names:
            attachments.append({'name': names['1'] or f'{title}(전문).hwp', 'url': download_url(m['seq'], 1)})
        if '2' in names:
            attachments.append({'name': names['2'] or f'{title}(신구조문대비표).hwp', 'url': download_url(m['seq'], 2)})
        cat = r['part']
        out_rules.append({
            'id': f'sw{r["node"]}' if r['node'] else f'seq{m["seq"]}',
            'seq': m['seq'],
            'title': title,
            'code': m['code'],
            'category': cat,
            'chapter': r['chapter'],
            'dept': m.get('dept', ''),
            'revised': fmt_date(m['date']),
            'url': preview_url(m['seq'], 1),
            'text': text,
            'attachments': attachments,
            'versions': [{'seq': v['seq'], 'date': fmt_date(v['date'])} for v in r['versions'][:30]],
        })
        if i % 20 == 0:
            log(f'  본문 {i}/{len(rules)}')

    out_rules.sort(key=lambda r: [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', r['code'] or 'zz')])
    data = {
        'source': LIST_PAGE,
        'fetchedAt': dt.datetime.now(KST).isoformat(timespec='seconds'),
        'rules': out_rules,
    }
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=0)
    log(f'저장: {args.out} (규정 {len(out_rules)}건, 본문 실패 {len(failed)}건)')
    if failed:
        log('  본문 실패: ' + ', '.join(failed))

    if args.changes:
        build_changes(c, out_rules, args, cache)


def build_changes(c, rules, args, cache):
    """최근 개정된 규정의 직전 판을 받아 개정 이력(changes.json)을 만든다(앱의 '최근 개정'·'개정 비교')."""
    since = (dt.date.today() - dt.timedelta(days=args.history_days)).strftime('%Y.%m.%d')
    by_date = {}
    for r in rules:
        vs = r.get('versions') or []
        if len(vs) < 2 or not r['revised'] or r['revised'] < since:
            continue
        prev = next((v for v in vs if v['seq'] != r['seq'] and v['date'] < r['revised']), None)
        if not prev:
            continue
        try:
            before = clean_text(fetch_text(c, prev['seq'], cache), r['title'])
        except Exception as e:
            log(f'  직전 판 실패: {r["title"]} — {e}')
            continue
        by_date.setdefault(r['revised'], []).append({'title': r['title'], 'kind': 'changed', 'before': before, 'after': r['text']})
    events = []
    for d in sorted(by_date, reverse=True)[:30]:
        y, m, dd = d.split('.')
        events.append({'at': f'{y}-{m}-{dd}T00:00:00+09:00', 'added': 0, 'removed': 0,
                       'changed': len(by_date[d]), 'rules': by_date[d]})
    with open(args.changes, 'w', encoding='utf-8') as f:
        json.dump({'events': events}, f, ensure_ascii=False, indent=0)
    log(f'개정 이력 저장: {args.changes} ({len(events)}회, 규정 {sum(len(e["rules"]) for e in events)}건)')


def main():
    ap = argparse.ArgumentParser(description='서원대학교 규정집 수집기')
    ap.add_argument('--out', default=os.path.join(HERE, '..', 'data', 'rules.json'))
    ap.add_argument('--delay', type=float, default=0.4, help='요청 사이 쉬는 시간(초)')
    ap.add_argument('--cache', default='', help='받은 HWP를 저장해 다시 쓰는 폴더')
    ap.add_argument('--changes', default='', help='최근 개정 이력(changes.json)도 만들 경로')
    ap.add_argument('--history-days', type=int, default=120, help='--changes: 최근 며칠 안의 개정을 담을지')
    ap.add_argument('--include-untree', action='store_true', help='분류 트리에 없는 목록 항목(폐지·옛 명칭 등)도 담기')
    ap.add_argument('--text', help='HWP 파일 하나를 텍스트로 바꿔 출력하고 끝냄')
    args = ap.parse_args()
    if args.text:
        print(hwp_to_text(args.text))
        return
    crawl(args)


if __name__ == '__main__':
    main()
