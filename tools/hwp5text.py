"""HWP 5.0 본문 텍스트 추출 (olefile만 사용). 표는 행마다 셀을 ' | '로 잇습니다."""
import struct
import zlib

try:
    import olefile
except ImportError:  # pragma: no cover
    raise SystemExit('필요한 패키지가 없습니다: pip install olefile')

TAG_PARA_HEADER = 66
TAG_PARA_TEXT = 67
TAG_CTRL_HEADER = 71
TAG_LIST_HEADER = 72
TAG_TABLE = 77

# 8 wchar(16바이트)를 차지하는 제어 문자
EXT_CTRL = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}


def _records(data):
    pos, n = 0, len(data)
    while pos + 4 <= n:
        (h,) = struct.unpack_from('<I', data, pos)
        pos += 4
        tag, level, size = h & 0x3FF, (h >> 10) & 0x3FF, (h >> 20) & 0xFFF
        if size == 0xFFF:
            (size,) = struct.unpack_from('<I', data, pos)
            pos += 4
        yield tag, level, data[pos:pos + size]
        pos += size


def _para_text(payload):
    out, i, n = [], 0, len(payload) // 2
    while i < n:
        (c,) = struct.unpack_from('<H', payload, i * 2)
        if c < 32:
            if c in EXT_CTRL:
                if c == 9:
                    out.append('\t')
                i += 8
                continue
            if c in (10,):
                out.append('\n')
            elif c in (24, 25, 26, 27, 28, 29, 30, 31):
                out.append(' ' if c in (30, 31) else '')
            i += 1
            continue
        out.append(chr(c))
        i += 1
    # 한자 확장 등 보조 평면 글자는 UTF-16 대리쌍으로 들어 있다 → 한 글자로 합친다
    return ''.join(out).encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')


def _section_streams(ole):
    names = []
    for e in ole.listdir():
        if len(e) == 2 and e[0] == 'BodyText' and e[1].startswith('Section'):
            names.append(e)
    names.sort(key=lambda e: int(e[1][7:] or 0))
    return names


def hwp_to_text(path_or_bytes):
    ole = olefile.OleFileIO(path_or_bytes)
    header = ole.openstream('FileHeader').read()
    flags = struct.unpack_from('<I', header, 36)[0]
    compressed = bool(flags & 1)
    if flags & 2:
        raise ValueError('암호가 걸린 HWP 파일입니다.')
    lines = []
    for name in _section_streams(ole):
        data = ole.openstream(name).read()
        if compressed:
            data = zlib.decompress(data, -15)
        lines.extend(_section_lines(data))
    ole.close()
    text = '\n'.join(lines)
    while '\n\n\n' in text:
        text = text.replace('\n\n\n', '\n\n')
    return text.strip()


def _section_lines(data):
    """문단은 한 줄씩. 표(level 기준)는 셀 문단을 모아 행 단위로 만듭니다."""
    lines = []
    tables = []  # 열린 표 스택: {level, rows, cols, cells:{(r,c):[text]}, cur}
    for tag, level, payload in _records(data):
        # 표가 끝났는지(더 얕은 level의 레코드가 나오면 닫힘)
        while tables and level <= tables[-1]['level']:
            t = tables.pop()
            rendered = _render_table(t)
            if tables:
                cur = tables[-1]['cur']
                if cur is not None:
                    tables[-1]['cells'].setdefault(cur, []).extend(rendered)
            else:
                lines.extend(rendered)
        if tag == TAG_CTRL_HEADER and payload[:4] == b' lbt':  # 'tbl ' 역순
            tables.append({'level': level, 'cells': {}, 'cur': None, 'rows': 0, 'cols': 0})
            continue
        if tag == TAG_TABLE and tables and tables[-1]['rows'] == 0 and len(payload) >= 8:
            tables[-1]['rows'], tables[-1]['cols'] = struct.unpack_from('<HH', payload, 4)
            continue
        if tag == TAG_LIST_HEADER and tables and level == tables[-1]['level'] + 1:
            # LIST_HEADER: nPara(2) ?(2) prop(4) | 셀 속성: col(2) row(2) colspan(2) rowspan(2) ...
            # 글자가 없는 칸도 자리를 남겨야 옆 칸이 밀리지 않으므로 여기서 칸을 등록한다.
            # 병합으로 가려진 칸은 LIST_HEADER가 없으므로 격자에서 빈 칸으로 남는다.
            if len(payload) >= 12:
                col, row = struct.unpack_from('<HH', payload, 8)
                tables[-1]['cur'] = (row, col)
                tables[-1]['cells'].setdefault((row, col), [])
            continue
        if tag == TAG_PARA_TEXT:
            t = _para_text(payload).rstrip('\r')
            if tables and tables[-1]['cur'] is not None:
                tables[-1]['cells'].setdefault(tables[-1]['cur'], []).append(t)
            elif not tables:
                lines.append(t)
        elif tag == TAG_PARA_HEADER and not tables:
            # 글자 없는 문단(빈 줄): PARA_HEADER의 nChars가 1(문단 끝만)이면 빈 줄
            if len(payload) >= 4:
                (nch,) = struct.unpack_from('<I', payload, 0)
                if (nch & 0x7FFFFFFF) <= 1:
                    lines.append('')
    while tables:
        t = tables.pop()
        rendered = _render_table(t)
        if tables and tables[-1]['cur'] is not None:
            tables[-1]['cells'].setdefault(tables[-1]['cur'], []).extend(rendered)
        else:
            lines.extend(rendered)
    return lines


def _render_table(t):
    """표를 행마다 ' | '로 이은 줄로 바꾼다.

    두 줄 이상인 표(별표 등)는 행×열 격자로 펼친다. 빈 칸과 병합으로 가려진 칸도 빈 문자열로
    자리를 지켜 모든 행의 칸 수가 같게 한다(병합된 내용은 첫 칸에만 한 번 쓴다).
    한 줄짜리 표는 대개 머리말·제목 상자라서 글자 있는 칸만 잇는다(수집기가 머리줄을 지울 수 있게)."""
    # 칸 안의 줄바꿈은 공백으로 바꾼다(한 칸이 여러 줄로 쪼개지면 표 한 행이 여러 줄로 갈라진다)
    cells = {k: ' '.join(x.strip() for x in v if x.strip()).replace('\r', '').replace('\n', ' ') for k, v in t['cells'].items()}
    if not any(cells.values()):
        return []
    # 크기는 표 머리(TABLE)의 행·열 수를 쓰고, 칸 주소가 그보다 크면 늘린다(병합 수는 크기 계산에 쓰지 않음).
    n_rows = max([t['rows']] + [r + 1 for r, _ in cells])
    n_cols = max([t['cols']] + [c + 1 for _, c in cells])
    grid = [[''] * n_cols for _ in range(n_rows)]
    for (r, c), text in cells.items():
        grid[r][c] = text
    out = ['']
    for row in grid:
        if not any(row):
            continue
        out.append(' | '.join(row if n_rows > 1 else [x for x in row if x]))
    out.append('')
    return out


if __name__ == '__main__':
    import sys
    sys.stdout.write(hwp_to_text(sys.argv[1]) + '\n')
