# CLAUDE.md — 서원대학교 규정집 앱 만들기 지침

이 폴더는 **서원대학교 규정집 모바일 웹앱**의 코드와 데이터 수집 도구입니다. 사용자(서원대 담당자)가
"CLAUDE.md대로 앱을 만들어줘"라고 하면 아래 순서대로 진행하세요. 사용자는 개발자가 아닐 수 있으니
각 단계 결과를 쉬운 말로 짧게 알리고, 사용자가 해야 하는 일(신청·클릭)은 정확한 화면 위치와 함께 안내하세요.

## 만드는 것

- 앱: `index.html`·`engine.js`(분석 엔진)·`sw.js`·`manifest.webmanifest`·`icon.svg` — 서버 없이 GitHub Pages에서 동작하는 정적 웹앱
- 데이터: `data/rules.json`(규정 전문), `data/changes.json`(최근 개정 이력), `data/laws.json`·`data/lawtext/`(국가법령정보센터 법령)
- 앱은 `data/` 파일만 읽습니다. **코드는 고치지 말고 데이터를 만드는 것이 기본 작업입니다.**

## 진행 순서

### 0. 환경 확인
- `python --version`(3.9 이상), `node --version`(점검용, 18 이상) 확인. 없으면 설치를 안내합니다(Windows: python.org, nodejs.org).
- `pip install -r tools/requirements.txt` (requests, olefile)
- 이 폴더가 git 저장소인지, GitHub 원격이 있는지 확인합니다(`git remote -v`). 없으면 7단계에서 만듭니다.

### 1. 규정 수집 (약 5~10분)
```bash
python tools/seowon_rules_crawler.py --cache .hwpcache --changes data/changes.json --history-days 120
```
- 서원대 규정집 분류 트리 → 규정별 최신 판 → HWP 받기 → 본문 추출 → `data/rules.json`
- `--cache .hwpcache`: 받은 HWP를 저장해 다시 실행할 때 빠르게 합니다(저장소에는 올리지 않음, `.gitignore`).
- 학교 서버 부담을 줄이려고 요청 사이 0.4초 쉽니다. `--delay`를 0.3 미만으로 줄이지 마세요.
- 네트워크가 막혀 `www.seowon.ac.kr`에 접속이 안 되면, 사용자에게 학교 PC 또는 인터넷이 되는 PC에서 실행하도록 안내합니다.

### 2. 수집 결과 점검
```bash
node tools/check_data.js
```
- 기대값(2026-11 기준): 규정 약 209건, 조문 약 3,600개, 조문을 못 나눈 규정 0건. 규정 수가 크게 다르거나
  "점검 통과"가 아니면 `docs/site-structure.md`를 보고 사이트 응답과 비교해 `tools/seowon_rules_crawler.py`를 고칩니다.
- 규정 몇 개(예: 학칙, 직제 규정)의 `text`를 직접 열어 조문이 제대로 나왔는지 눈으로 확인합니다.

### 3. 국가법령정보센터 법령 확인 (사용자 준비 필요)
사용자에게 **국가법령정보센터 OPEN API 인증키(OC)**를 물어봅니다. 없으면 이렇게 안내하세요.
1. https://open.law.go.kr → 로그인 → OPEN API → OPEN API 신청
2. 인증키(OC)는 직접 정하는 값(보통 이메일 아이디). **서버장비의 IP주소**에 이 프로그램을 실행할 PC의 공인 IP 등록
   (네이버에서 "내 IP" 검색으로 확인)
3. 공동활용 법령종류: '대한민국 현행법령'과 '현행 행정규칙'의 목록 JSON·본문 JSON 선택 → 승인까지 시간이 걸릴 수 있음

인증키를 받으면:
```bash
python tools/law_check.py --oc 인증키 --text-dir data/lawtext
node tools/check_data.js
```
- 기대값: 인용 법령 약 120개 중 현행 약 100개 확인, 전체 조문 약 100개.
- 'API 오류'·HTML 응답이면 등록한 IP와 실행 PC의 IP가 같은지, 신청 승인이 끝났는지 확인하게 합니다.
- **인증키를 파일·커밋·메시지에 남기지 마세요.** 명령줄 인자나 환경변수 `LAW_OC`로만 씁니다.
- 인증키가 아직 없으면 이 단계를 건너뛰고 나중에 하도록 안내합니다(앱은 법령 비교 없이도 동작).

### 4. 앱을 내 PC에서 확인
```bash
python -m http.server 8000
```
브라우저에서 http://localhost:8000 을 열어 검색·규정목록·검증 탭이 동작하는지 확인하게 합니다
(파일을 더블클릭해 `file://`로 열면 데이터를 못 읽습니다).

### 5. GitHub에 올리고 공개 (GitHub Pages)
1. 사용자에게 GitHub 계정이 있는지 묻고, 없으면 가입을 안내합니다.
2. 새 저장소 만들기(예: `seowon-rules`). 공개(Public) 저장소여야 무료 Pages를 쓸 수 있습니다.
   `gh` CLI가 있으면 `gh repo create`, 없으면 github.com에서 New repository.
3. 이 폴더를 커밋하고 `main` 브랜치에 push. `.hwpcache/`는 올리지 않습니다.
4. 저장소 Settings → Pages → Source: Deploy from a branch → Branch: `main`, 폴더 `/ (root)` → Save
5. 1~2분 뒤 `https://아이디.github.io/저장소이름/` 에서 앱이 열립니다. 휴대폰 브라우저에서 열고
   메뉴의 **홈 화면에 추가**를 누르면 앱처럼 설치됩니다.

### 6. 이후 업데이트 방법 안내
- 규정이 개정되면 같은 PC에서 `tools/규정업데이트.bat`을 실행 → 처음 한 번 OC, GitHub 토큰, 저장소(`아이디/저장소이름`) 입력
  → **업데이트 시작**. 수집 → 법령 확인 → 바뀐 데이터 업로드가 한 번에 됩니다.
- GitHub 토큰: GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens →
  이 저장소만 선택 → Permissions의 Contents: Read and write
- 또는 Claude에게 "규정 데이터 업데이트해줘"라고 하면 1~3단계를 다시 하고 커밋·push합니다.

## 지켜야 할 것

- 인증키(OC)·GitHub 토큰은 절대 저장소에 넣지 않습니다(업데이트 프로그램은 `%APPDATA%\seowon-rules\config.json`에만 저장).
- 학교 서버에 짧은 시간에 많은 요청을 보내지 않습니다(기본 간격 유지, 실패 시 반복 재실행 자제).
- 자동 점검 결과는 문언 패턴으로 찾은 **검토 후보**이며 법률 자문이 아닙니다. 사용자에게 결과를 전할 때 이 점을 밝힙니다.
- 앱 코드(`index.html`, `engine.js`)를 고칠 때는 사용자가 요청한 경우에만, 고친 뒤 4단계로 확인합니다.

## 폴더 구조

```
index.html, engine.js, sw.js, manifest.webmanifest, icon.svg   앱
data/   rules.json, changes.json, laws.json, lawtext/          수집 결과 (1·3단계에서 생성)
        topics.json(분야별 기본 법령), reviews.json(검토 의견, 처음엔 비어 있음),
        lawchanges.json(상위법 개정 알림), sample.json(체험용 예시)
tools/  seowon_rules_crawler.py, hwp5text.py   규정 수집·HWP 본문 추출
        law_check.py                          국가법령정보센터 법령 확인
        check_data.js                         수집 결과 점검
        update_gui.py, 규정업데이트.bat         한 번에 업데이트(Windows)
        review_queue.js                       검토 의견 대기열(선택)
docs/site-structure.md                        서원대 규정집 사이트 구조(수집기가 고장 났을 때 참고)
```

## 문제 해결

| 증상 | 원인·조치 |
| --- | --- |
| `No module named olefile/requests` | `pip install -r tools/requirements.txt` |
| 규정 수가 크게 적음, 본문 실패 | 사이트 구조 변경 가능 → `docs/site-structure.md`와 실제 응답 비교 후 수집기 수정 |
| `HWP 5.0 파일이 아닙니다` | 학교가 HWPX/PDF로 올린 규정. `hwp5text.py`에 HWPX(zip 안 `Contents/section*.xml`) 읽기 추가 |
| 법령 확인 'API 오류' | OC 오타, IP 미등록·불일치, 신청 미승인 |
| 앱에서 '데이터 없음' | `data/rules.json`이 push됐는지, Pages 배포가 끝났는지 확인 |
| 휴대폰에 예전 내용이 보임 | 앱을 완전히 닫았다 다시 열기(오프라인 캐시는 다음 실행 때 갱신) |
