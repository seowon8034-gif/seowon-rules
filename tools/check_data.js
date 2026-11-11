#!/usr/bin/env node
/*
 * 수집 결과 점검: node tools/check_data.js
 * data/rules.json(+ data/laws.json)을 앱과 같은 엔진으로 읽어 규정·조문 수, 조문을 못 나눈 규정,
 * 정합성 점검 요약을 보여준다. 문제가 크면 종료 코드 1.
 */
const fs = require('fs');
const path = require('path');
const E = require(path.join(__dirname, '..', 'engine.js'));
const DATA = path.join(__dirname, '..', 'data');
const read = f => JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf8'));

if (!fs.existsSync(path.join(DATA, 'rules.json'))) {
  console.error('data/rules.json이 없습니다. 먼저 python tools/seowon_rules_crawler.py 를 실행하세요.');
  process.exit(1);
}
const rd = read('rules.json');
const laws = fs.existsSync(path.join(DATA, 'laws.json')) ? read('laws.json') : null;
const index = E.buildIndex(rd.rules, laws ? laws.laws : {});
const rules = index.rules;
const arts = rules.reduce((s, r) => s + r.articles.length, 0);
const noArts = rules.filter(r => !r.articles.length || r.articles[0].key === 'body');
const empty = rd.rules.filter(r => !r.text || r.text.length < 200);
const cats = {};
rules.forEach(r => { cats[r.category || '(분류 없음)'] = (cats[r.category || '(분류 없음)'] || 0) + 1; });

console.log(`수집 시각: ${rd.fetchedAt}`);
console.log(`규정 ${rules.length}건 · 조문 ${arts}개`);
console.log('분류별:', Object.entries(cats).map(([k, v]) => `${k} ${v}`).join(', '));
console.log(`조문(제N조)을 나누지 못한 규정 ${noArts.length}건${noArts.length ? ': ' + noArts.map(r => r.title).join(', ') : ''}`);
console.log(`본문이 거의 없는 규정 ${empty.length}건${empty.length ? ': ' + empty.map(r => r.title).join(', ') : ''}`);
if (laws) {
  const L = Object.values(laws.laws);
  console.log(`법령 확인: ${L.length}개 중 현행 ${L.filter(x => x.found).length}개, 전체 조문 ${L.filter(x => x.text).length}개 (${laws.checkedAt})`);
  const missingText = L.filter(x => x.text && !fs.existsSync(path.join(DATA, x.text)));
  if (missingText.length) console.log(`  ! lawtext 파일 없음 ${missingText.length}개: ${missingText.map(x => x.name).join(', ')}`);
} else {
  console.log('법령 확인: data/laws.json 없음 (python tools/law_check.py --oc 인증키 --text-dir data/lawtext)');
}
const v = E.validate(index);
console.log(`정합성 점검: 오류 ${v.stats.error} · 주의 ${v.stats.warn} · 참고 ${v.stats.info} · 위임·인용 연결 ${v.stats.edges}개`);

const bad = rules.length < 150 || noArts.length > rules.length * 0.05 || empty.length > rules.length * 0.05;
if (bad) {
  console.error('\n문제: 규정 수가 너무 적거나 본문·조문을 읽지 못한 규정이 많습니다. 사이트 구조가 바뀌었는지 확인하세요(docs/site-structure.md).');
  process.exit(1);
}
console.log('\n점검 통과');
