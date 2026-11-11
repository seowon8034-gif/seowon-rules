#!/usr/bin/env node
/*
 * 검토 의견(data/reviews.json) 대기열 도구.
 *
 * 앱의 자동 점검이 찾는 후보(정합성 오류·주의, 인용 조 내용 확인, 상위법 내용 분석, 법과 숫자 다름)를 모아
 * 이미 검토한 의견과 비교한다. 검토할 때의 원문 지문(hash)이 지금과 다르면 "재검토", 의견이 없으면 "새 후보",
 * 더는 후보가 아니면 "해결됨"이다. 매일 새벽 예약된 Claude 검토 작업이 이 도구로 대기열을 만들고 결과를 반영한다.
 *
 *   node review_queue.js queue [--out 폴더]     대기열 만들기 → 폴더/queue.json, queue.txt (기본: 현재 폴더)
 *   node review_queue.js apply 판정.json         판정을 reviews.json에 반영(지문·검토일 기록, 해결된 항목 삭제)
 *   node review_queue.js rehash                  지문이 없는 기존 의견에 지금 원문의 지문을 기록
 *
 * 전체 검토 의견(키가 manual|로 시작): 자동 점검 후보가 아니라 규정 전체를 읽고 찾은 의견이다.
 * 판정에 ref: {rule: 규정명, art: 조 키(예: "37", "41-2"), law: 법령명(없으면 생략), lawArt: 법령 조 키} 를 함께 준다.
 * 해결됨으로 지우지 않고, 규정 조문이나 대상 법령 조문이 바뀌면 재검토 대기열에 다시 넣는다.
 *
 * 판정.json: { "키": { "verdict": "outdated|check|ok|violation", "summary": "…", "reason": "…", "suggestion": "…" }, … }
 */
const fs = require('fs');
const path = require('path');
const E = require(path.join(__dirname, '..', 'engine.js'));

const DATA = path.join(__dirname, '..', 'data');
const read = f => JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf8'));
const cut = (s, n) => (String(s || '').length > n ? String(s).slice(0, n) + '…' : String(s || ''));
const todayKst = () => new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10);

function candidates() {
  const rulesData = read('rules.json');
  const lawsData = read('laws.json');
  const L = lawsData.laws;
  const catalog = fs.existsSync(path.join(DATA, 'topics.json')) ? read('topics.json') : null;
  const index = E.buildIndex(rulesData.rules, L);
  const v = E.validate(index);
  const an = v.analysis;
  const cache = new Map();
  const full = k => {
    if (!cache.has(k)) cache.set(k, Object.assign({ name: L[k].name, url: L[k].url }, JSON.parse(fs.readFileSync(path.join(DATA, L[k].text), 'utf8'))));
    return cache.get(k);
  };
  const out = [];
  // 1) 정합성 점검: 오류·주의와 '인용 조 내용 확인'(참고)
  // 자동 정합성 점검 화면은 전체 검토로 대체했으므로, 상위법 비교의 '인용 상태'에 보이는 법령 인용 항목만 검토한다
  for (const it of v.issues) {
    if (!(/^law-/.test(it.type) || (it.type === 'cite-unrelated' && it.edge && !it.edge.to.rule)) || it.article.addenda) continue;
    const e = it.edge;
    const target = e ? (e.to.rule ? `「${e.to.rule.title}」` : `「${(e.law && e.law.name) || e.to.name}」`) + (e.to.artKey ? ' ' + E.artLabel(e.to.artKey) : '') : '';
    let targetText = E.issueTargetText(it);
    if (!targetText && e && e.law && !e.law.found) targetText = `(국가법령정보센터에 없음) 비슷한 현행 법령: ${(e.law.similar || []).join(', ')}`;
    out.push({ key: E.issueReviewKey(it), hash: E.issueReviewHash(it), cat: 'issue:' + it.type, level: it.level,
      rule: it.rule.title, art: `${it.article.no}${it.article.title ? '(' + it.article.title + ')' : ''}`,
      msg: it.message, detail: it.detail, phrase: e ? e.phrase : '', ruleText: it.article.text, target, targetText });
  }
  // 2) 상위법 비교: 내용 분석과 법 숫자 기준 다름 (법령 전체 조문 기준)
  for (const r of index.rules) {
    if (r.abolished) continue;
    const rel = E.relatedLaws(index, r, an, catalog).filter(x => x.law && x.law.found && x.law.text);
    if (!rel.length) continue;
    const texts = new Map(rel.map(x => [x.key, full(x.key)]));
    const { similar } = E.compareWithLaws(index, r, an, texts);
    for (const f of E.contentFindings(r, similar, texts)) {
      const lawText = f.lawArt ? f.lawArt.text : f.lawText;
      out.push({ key: E.reviewKey('content:' + f.kind, r, f.article.key, f.lawName, f.artKey), hash: E.reviewHash(f.article.text, lawText),
        cat: 'content:' + f.kind, rule: r.title, art: `${f.article.no}${f.article.title ? '(' + f.article.title + ')' : ''}`,
        msg: f.message, focus: f.lawText, ruleText: f.article.text,
        target: `「${f.lawName}」 ${f.lawArt ? (f.lawArt.no || E.artLabel(f.artKey)) + (f.lawArt.title ? '(' + f.lawArt.title + ')' : '') : E.artLabel(f.artKey)}`, targetText: lawText });
    }
    for (const p of E.limitPairs(r, texts)) {
      if (p.status === 'same') continue;
      out.push({ key: E.reviewKey('limit', r, p.article.key, p.lv.name, p.lv.ak, p.rv.raw), hash: E.reviewHash(p.article.text, p.lv.art.text),
        cat: 'limit:' + p.status, rule: r.title, art: `${p.article.no}${p.article.title ? '(' + p.article.title + ')' : ''}`,
        msg: `규정 ${p.rv.raw} ↔ 법 ${p.lv.raw}`, focus: `규정: ${p.line}\n법: ${p.lv.line}`, ruleText: p.article.text,
        target: `「${p.lv.name}」 ${p.lv.art.no || E.artLabel(p.lv.ak)}${p.lv.art.title ? '(' + p.lv.art.title + ')' : ''}`, targetText: p.lv.art.text });
    }
  }
  // 같은 키는 하나만(같은 인용이 한 조에 두 번 나오는 경우)
  const seen = new Set();
  return { list: out.filter(c => !seen.has(c.key) && seen.add(c.key)), basis: `규정 ${String(rulesData.fetchedAt || '').slice(0, 10)} 수집본, 법령 ${String(lawsData.checkedAt || '').slice(0, 10)} 확인본` };
}

// 전체 검토 의견의 원문: 규정 조문 + (있으면) 법령 조문(전체 조문 파일 우선)
function manualContext(ctx, ref) {
  const rule = E.findRuleByName(ctx.index, ref.rule);
  const art = rule && rule.articles.find(a => a.key === String(ref.art));
  let lawText = '';
  if (ref.law) {
    const k = E.normTitle(ref.law);
    const L = ctx.laws[k];
    if (L && L.text && fs.existsSync(path.join(DATA, L.text))) {
      const a = JSON.parse(fs.readFileSync(path.join(DATA, L.text), 'utf8')).articles[ref.lawArt];
      lawText = a ? a.text : '';
    } else if (L && L.articles && L.articles[ref.lawArt]) lawText = L.articles[ref.lawArt].text || '';
  }
  return { rule, art, lawText, hash: E.reviewHash(art ? art.text : '', lawText) };
}

function manualCtx() {
  const L = read('laws.json').laws;
  return { index: E.buildIndex(read('rules.json').rules, L), laws: L };
}

function loadReviews() {
  const f = path.join(DATA, 'reviews.json');
  return fs.existsSync(f) ? JSON.parse(fs.readFileSync(f, 'utf8')) : { items: {} };
}

function saveReviews(rv) {
  fs.writeFileSync(path.join(DATA, 'reviews.json'), JSON.stringify(rv, null, 1) + '\n');
}

function queue(outDir) {
  const { list, basis } = candidates();
  const rv = loadReviews();
  const now = new Set(list.map(c => c.key));
  const q = [];
  for (const c of list) {
    const old = rv.items[c.key];
    if (!old) q.push(Object.assign({ status: 'new' }, c));
    else if (old.hash && old.hash !== c.hash) q.push(Object.assign({ status: 'stale', previous: { verdict: old.verdict, summary: old.summary, reason: old.reason, at: old.at } }, c));
  }
  const mctx = manualCtx();
  for (const [key, it] of Object.entries(rv.items)) {
    if (!key.startsWith('manual|') || !it.ref) continue;
    const m = manualContext(mctx, it.ref);
    if (it.hash && it.hash !== m.hash) {
      q.push({ status: 'stale', key, hash: m.hash, cat: 'manual', rule: it.rule, art: it.article, msg: `전체 검토 의견 재검토: ${it.summary}`,
        previous: { verdict: it.verdict, summary: it.summary, reason: it.reason, at: it.at }, ref: it.ref,
        ruleText: m.art ? m.art.text : '(이 조문이 없어졌습니다)', target: it.ref.law ? `「${it.ref.law}」 ${E.artLabel(it.ref.lawArt)}` : '', targetText: m.lawText });
    }
  }
  const resolved = Object.keys(rv.items).filter(k => !now.has(k) && !k.startsWith('manual|'));
  const res = { createdAt: new Date().toISOString(), basis, counts: { candidates: list.length, new: q.filter(x => x.status === 'new').length,
    stale: q.filter(x => x.status === 'stale').length, resolved: resolved.length, unchanged: list.length - q.length }, resolved, items: q };
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'queue.json'), JSON.stringify(res, null, 1));
  let txt = `검토 대기열 (${basis})\n새 후보 ${res.counts.new} · 재검토 ${res.counts.stale} · 해결됨 ${res.counts.resolved} · 변경 없음 ${res.counts.unchanged}\n`
    + `\n[문체 규칙 — 반드시 지킴] summary는 제목처럼 짧은 명사형 문구로 씁니다. reason·suggestion은 모든 문장을 '~합니다/~입니다/~있습니다/~됩니다' 존댓말로 끝냅니다. '~한다', '~이다', '~있다' 같은 반말이나 '수정.', '추가.', '바로잡음.' 같은 개조식 끝맺음은 쓰지 않습니다(예: '…로 수정합니다.', '…를 추가합니다.').\n`;
  q.forEach((x, i) => {
    txt += `\n===== #${i} [${x.status}] [${x.cat}] ${x.rule} ${x.art}\nKEY ${x.key}\nMSG ${x.msg}` +
      (x.detail ? `\nDETAIL ${x.detail}` : '') + (x.phrase ? `\nPHRASE ${x.phrase}` : '') + (x.focus ? `\nFOCUS ${x.focus}` : '') +
      (x.previous ? `\nPREVIOUS [${x.previous.verdict}] ${x.previous.summary} (${x.previous.at || ''})` : '') +
      `\nRULE ${cut(x.ruleText, 1500)}\nTARGET ${x.target}\n${cut(x.targetText, 2500)}\n`;
  });
  fs.writeFileSync(path.join(outDir, 'queue.txt'), txt);
  console.log(JSON.stringify(res.counts));
}

function apply(file) {
  const verdicts = JSON.parse(fs.readFileSync(file, 'utf8'));
  const { list, basis } = candidates();
  const byKey = new Map(list.map(c => [c.key, c]));
  const rv = loadReviews();
  const at = todayKst();
  const mctx = manualCtx();
  let n = 0;
  for (const [key, v] of Object.entries(verdicts)) {
    let c = byKey.get(key);
    if (!c && key.startsWith('manual|')) {
      const ref = v.ref || (rv.items[key] && rv.items[key].ref);
      const m = ref ? manualContext(mctx, ref) : null;
      if (!m || !m.art) { console.error('전체 검토 의견의 규정·조를 찾지 못해 건너뜀:', key); continue; }
      c = { rule: m.rule.title, art: `${m.art.no}${m.art.title ? '(' + m.art.title + ')' : ''}`, cat: 'manual', hash: m.hash, ref };
    }
    if (!c) { console.error('후보에 없는 키라 건너뜀:', key); continue; }
    if (!['outdated', 'check', 'ok', 'violation'].includes(v.verdict)) { console.error('알 수 없는 판정이라 건너뜀:', key, v.verdict); continue; }
    rv.items[key] = Object.fromEntries(Object.entries({ verdict: v.verdict, summary: v.summary, reason: v.reason, suggestion: v.suggestion,
      rule: c.rule, article: c.art, kind: c.cat, hash: c.hash, ref: c.ref, at }).filter(([, x]) => x));
    n++;
  }
  // 더는 후보가 아닌(규정·법 개정으로 해결된) 항목은 지운다
  const now = new Set(list.map(c => c.key));
  const removed = Object.keys(rv.items).filter(k => !now.has(k) && !k.startsWith('manual|'));
  removed.forEach(k => delete rv.items[k]);
  rv.reviewedAt = at;
  rv.basis = basis;
  rv.reviewer = rv.reviewer || 'Claude 검토';
  saveReviews(rv);
  console.log(JSON.stringify({ applied: n, removed: removed.length, total: Object.keys(rv.items).length }));
}

function rehash() {
  const { list } = candidates();
  const rv = loadReviews();
  let n = 0;
  for (const c of list) {
    const it = rv.items[c.key];
    if (it && !it.hash) { it.hash = c.hash; it.at = it.at || rv.reviewedAt; n++; }
  }
  saveReviews(rv);
  console.log(JSON.stringify({ hashed: n }));
}

const [cmd, arg, arg2] = process.argv.slice(2);
if (cmd === 'queue') queue(arg === '--out' ? arg2 : process.cwd());
else if (cmd === 'apply' && arg) apply(arg);
else if (cmd === 'rehash') rehash();
else {
  console.error('사용법: node review_queue.js queue [--out 폴더] | apply 판정.json | rehash');
  process.exit(1);
}
