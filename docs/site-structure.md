# 서원대학교 규정집 사이트 구조 (수집기 기준, 2026-11 확인)

수집기 `tools/seowon_rules_crawler.py`가 쓰는 주소와 응답 형식입니다. 사이트가 바뀌어 수집이 실패하면
이 문서와 실제 응답을 비교해 수집기를 고칩니다.

- 규정집 화면: `https://www.seowon.ac.kr/seowon/301/subview.do`
- 화면 안 숨은 값: `regltnSiteId=seowon`, `regltnFnctNo=7`, `regltnClSeq=3`(최상위 분류)
- 모든 요청에 브라우저 User-Agent와 `X-Requested-With: XMLHttpRequest`(AJAX 주소)를 붙입니다.

## 1. 분류 트리

```
GET /regltn/seowon/7/get_node/operation?id=3
→ [{"id":4,"text":"제1편 학교법인","children":true}, {"id":5,"text":"제2편 학칙",...}, ...]
```

- `id`를 바꿔 하위 노드를 받습니다. `제N편`·`제N장` 노드 아래의 나머지 노드가 규정입니다.
- 규정 노드도 `children: true`일 수 있습니다(예: 미래창조연구원 규정 아래 연구소 운영세칙).
- 2026-11 기준 규정 노드 209개, 8편(학교법인·학칙·행정·대학원·위원회·부속기관 및 산학협력단·부설연구소·기타).
- `.do`를 붙이면 오류 페이지가 옵니다(`operation.do?id=%23`만 최상위를 돌려줌).

## 2. 규정별 제·개정 이력

```
POST /regltn/seowon/7/registView.do   (id=<노드 id>&parent=<상위 노드 id>)
→ HTML 표: 최신 판부터 10건씩(쪽 넘김 있음, 첫 쪽만 사용)
```

행마다:

```html
<a href="#none" onclick="jf_regltn_view('6445');">직제 규정</a></td>
<td>3-1-01</td>            ← 규정번호
<td>2026-10-28</td>        ← 제/개/폐정일
...
<tr class="table-hide" id="regltnView_6445"> … 소관부서, 현행규정·신구대비표 파일 이름 …
```

`6445`가 그 판의 번호(seq)입니다.

## 3. 첫 화면 목록 (참고용)

```
GET /seowon/301/subview.do          (1쪽)
POST /regltn/seowon/7/view.do       (page=2 … 26)
```

모든 규정의 최근 판 260건이 날짜순으로 나오지만, **폐지되거나 이름이 바뀐 옛 규정도 섞여 있습니다**.
그래서 분류 트리에 있는 규정만 현행으로 담고, 목록은 판 번호 확인에만 씁니다(`--include-untree`로 옛 규정 포함 가능).

## 4. 파일

```
GET /regltn/seowon/7/{seq}/1/download.do     현행 규정(전문) HWP
GET /regltn/seowon/7/{seq}/2/download.do     신구조문대비표 HWP
GET /regltn/seowon/7/{seq}/1/synapView.do    미리보기(웹 화면) — 앱의 '원문 보기'
```

HWP는 HWP 5.0(OLE) 형식입니다. `tools/hwp5text.py`가 `BodyText/Section*` 스트림을 풀어(zlib raw deflate)
문단 텍스트(PARA_TEXT, 태그 67)를 뽑고, 표(CTRL_HEADER `tbl `·TABLE·LIST_HEADER)는 행마다 셀을 ` | `로 잇습니다.
HWPX·PDF가 올라오면 지원하지 않으므로 오류로 알립니다.

## 5. 본문 형식 (앱 엔진이 읽는 방식)

```
직제 규정
전부개정 2023.06.27. 개정 2023.08.30. …          ← 머리말(제정·개정 날짜)
제1장 총칙
제1조(목적) 이 규정은 「학교법인 서원학원 정관」…   ← 조문: "제N조(제목) 본문"
② …  <개정 2025.4.30.>                             ← 항, 조문 개정 표시
부칙 <2026.2.26.>                                  ← 부칙(개정마다)
[별표] …                                           ← 표는 "셀 | 셀"
```

첫 줄의 `3-1-01 「직제 규정」 (개정 2026.10.28.)` 머리줄과 `/` 줄은 수집기가 지웁니다(규정번호·개정일은 별도 항목).
