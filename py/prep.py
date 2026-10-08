"""공통 도우미: 인코딩 변환, 열 이름 표준화, 파일 읽기.

원본 CSV는 대부분 CP949이고 확장자만 .csv인 엑셀도 섞여 있어서,
UTF-8 사본을 만든 뒤 같은 함수로 읽는다.

사용: import sys; sys.path.append("../py"); from prep import to_utf8, std_name, read_any
"""
import codecs
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# 인코딩 변환 (01 문서 0-4)
# ---------------------------------------------------------------------------

def sniff(path, n=200_000):
    """파일 앞부분을 보고 형식·인코딩을 추정한다"""
    with open(path, "rb") as fh:
        raw = fh.read(n)
    if raw[:2] == b"PK":
        return "zip/xlsx"                          # 확장자만 .csv인 압축·엑셀 파일
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    if raw.lstrip()[:1] == b"<":
        return "html"                              # 다운로드가 실패해 오류 페이지가 저장된 경우
    for enc in ("utf-8-sig", "cp949"):
        try:
            codecs.getincrementaldecoder(enc)().decode(raw, final=False)
            return enc
        except UnicodeDecodeError:
            pass
    return "mixed"                                 # 대부분 맞는데 깨진 바이트가 섞인 파일


def _best_guess(path, n=5_000_000):
    """깨진 바이트가 섞인 파일용. utf-8과 cp949 중 대체 글자가 적게 나오는 쪽을 고른다"""
    with open(path, "rb") as fh:
        raw = fh.read(n)
    return min(("utf-8-sig", "cp949"), key=lambda e: raw.decode(e, errors="replace").count("�"))


def _copy(src, dst, enc, errors):
    n_bad = 0
    with open(src, encoding=enc, errors=errors, newline="") as fi, \
         open(dst, "w", encoding="utf-8", newline="") as fo:
        for line in fi:
            if errors == "replace":
                n_bad += line.count("�")
            fo.write(line)
    return n_bad


def to_utf8(src, dst):
    """원본은 그대로 두고 UTF-8 사본을 만든다. 반환: (인코딩, 깨진 글자 수)"""
    enc = sniff(src)
    if enc in ("zip/xlsx", "html"):
        return enc + " → 변환 안 함", None
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    if enc != "mixed":
        try:
            return enc, _copy(src, dst, enc, "strict")
        except UnicodeDecodeError:                 # 앞부분은 멀쩡하고 뒤에서 깨진 경우
            pass
    enc = _best_guess(src)
    return enc + " (깨진 글자는 �로 대체)", _copy(src, dst, enc, "replace")


def data_files(folder):
    """폴더 안의 CSV·엑셀 파일을 이름순으로 돌려준다(.DS_Store 같은 숨김 파일 제외)"""
    return sorted(p for p in Path(folder).iterdir()
                  if p.suffix.lower() in (".csv", ".xlsx", ".xls") and not p.name.startswith((".", "~$")))


def make_utf8_folder(src_dir, dst_dir):
    """raw 폴더를 interim/<폴더>_utf8로 옮긴다.

    CSV는 UTF-8 사본을 만들고 엑셀은 그대로 복사한다. 이름만 .csv인 엑셀은 .xlsx로 바꿔 복사한다.
    """
    import shutil
    dst_dir = Path(dst_dir); dst_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for f in data_files(src_dir):
        if f.suffix.lower() in (".xlsx", ".xls"):
            shutil.copy(f, dst_dir / f.name); how = "엑셀 → 복사"
        else:
            how, n_bad = to_utf8(f, dst_dir / f.name)
            if how.startswith("zip/xlsx"):              # 이름만 .csv인 엑셀
                shutil.copy(f, dst_dir / (f.stem + ".xlsx")); how = "이름만 csv인 엑셀 → .xlsx로 복사"
            elif n_bad:
                how += f", 깨진 글자 {n_bad}"
        rows.append({"file": f.name, "MB": round(f.stat().st_size / 1e6, 1), "how": how})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 열 이름 표준화와 파일 읽기 (02 문서 1A-3)
# ---------------------------------------------------------------------------

def std_name(df, new, candidates):
    """후보 이름 중 처음 맞는 열을 표준 이름으로 바꾼다"""
    for c in candidates:
        if c in df.columns:
            return df.rename(columns={c: new})
    return df


def read_any(path, **kw):
    """CSV(UTF-8 사본)와 엑셀을 같은 방식으로 읽는다. 엑셀 시트가 여러 개면 합친다"""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xls"):
        sheets = pd.read_excel(path, dtype=str, sheet_name=None, **kw)
        for name, s in sheets.items():
            s.columns = s.columns.astype(str).str.strip()
        if len(sheets) > 1:
            print(f"{path.name}: 시트 {len(sheets)}개 {list(sheets)} → 합침")
        x = pd.concat(sheets.values(), ignore_index=True)
    else:
        x = pd.read_csv(path, dtype=str, encoding="utf-8", **kw)
        x.columns = x.columns.str.strip()
    return x
