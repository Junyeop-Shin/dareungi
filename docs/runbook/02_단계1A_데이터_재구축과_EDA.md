# 02. 단계 1A — 데이터 재구축과 고장률 EDA (Python)

> 기간: 5~6일
> 선행 조건: 01 문서 완료 (인코딩·스키마 확인, `schema_map.csv`, `py/seoul_api.py` 있음) — 2026-10-01 완료, 결과는 `docs/phase0_result.md`
> 산출물:
> - `data/processed/daily_master_v2.parquet` (+ `.csv`)
> - `output/tables/eda_*.csv`, `output/figures/eda_*.png`
> - 확정 변수 목록(체크포인트 B), 결정 로그

---

## 사람과 AI의 역할

> 규칙: AI는 실행·검증·제안, 사람은 결정. `[결정]` 표시에서 AI는 선택지와 근거를 제시하고 멈춘다(`/checkpoint`). 전체 규약은 `docs/WORKFLOW_사람-AI_역할분담.md`.

| AI가 한다 | 사람이 결정한다 `[결정]` |
|---|---|
| 파일 점검·UTF-8 변환·병합·파생변수 코드, 연도별 합계 대조, EDA 표·그림 생성, 변수 후보표 초안 | 2015~2017 대여소별 파일 처리, 이상값·반기 경계일 처리, 결측 보간 방식, 체크포인트 A(레짐)·B(변수 목록), 개입 이벤트 포함 범위 |

---

## 목표

1. 2016-01-01 ~ 2026-06-30 일별 마스터를 **처음부터 스크립트로** 다시 만든다. 고장 유형 전체, 노출 3종(건수·거리·시간), 확장 기상, 달력, 운영·개입 변수를 넣는다.
2. 모든 탐색을 **건수가 아니라 고장률(노출 대비)** 기준으로 다시 한다.
3. 다음 단계 모형에 넣을 변수 목록을 여기서 닫는다.

## 이 단계가 답하는 질문

- 2025년 신고 감소는 어떤 레짐 변화인가? (전 부품 고르게 vs 특정 부품 집중)
- 노출을 통제해도 남는 패턴(요일, 계절, 기온, 전날 비)은 무엇인가?
- 각 변수는 **예측 시점에 미래 값을 알 수 있는가?**

---

## 1A-1. 환경 (반나절)

01에서 만든 가상환경에 단계 1용 패키지를 더한다.

```bash
cd ~/dareungi && source .venv/bin/activate
pip install statsmodels scipy scikit-learn lightgbm ruptures seaborn
pip freeze > requirements.txt
```

- [ ] `git init`, `.gitignore`에 `.env`, `.venv/`, `data/`, `*.duckdb`
- [ ] 1차 R 스크립트와 `dareungi_master.csv`는 `legacy/`로 옮겨 참고만 한다
- [ ] 노트북은 `notebooks/1A_build_master.ipynb`, `notebooks/1A_eda.ipynb` 두 개로 나눈다. 반복해서 쓰는 함수는 `py/prep.py`에 넣는다

공통 첫 셀:

```python
%load_ext autoreload
%autoreload 2                                # py/*.py를 고치면 커널 재시작 없이 다시 읽는다

import sys; sys.path.append("../py")        # 노트북이 notebooks/ 안에 있을 때
import numpy as np, pandas as pd
from pathlib import Path
import matplotlib.pyplot as plt
plt.rcParams["font.family"] = "AppleGothic"  # 맥 한글 글꼴
plt.rcParams["axes.unicode_minus"] = False
ROOT = Path("..")                            # 프로젝트 폴더

from prep import to_utf8, std_name, read_any, data_files, make_utf8_folder
```

커널을 재시작하면 앞에서 불러온 것이 모두 사라진다. `NameError: name '...' is not defined`가 나면 이 첫 셀부터 다시 실행한다.

---

## 1A-2. 데이터 수집 (1~2일)

| 데이터 | 출처 | 기간 | 저장 위치 | 비고 |
|---|---|---|---|---|
| 고장신고 | OA-15644 | 2015 ~ 2026-06 | `raw/failure/` | 2020-11 ~ 2026-06 CSV 13개는 단계 0에서 변환 완료. 2015 ~ 2020-10은 xlsx 1개(`내역_2015_2020.10.xlsx`) → `read_any`로 읽는다(시트가 여러 개면 합쳐 읽고 알려 준다) |
| 일별 대여건수 | 1차와 같은 출처(OA-14994 등) | 2016-01 ~ 2026-06 | `raw/rent_daily/` | |
| 이용정보(일별) | OA-15246 | 2017 ~ 2026-06 | `raw/use_daily/` | 대여소×일 단위. 이동거리·이용시간 → 노출 후보 |
| ASOS 일자료, 서울(108) | 기상자료개방포털 → 종관기상관측(ASOS) 일자료 | 2016 ~ 2026-06 | `raw/weather/` | 아래 항목 선택 |
| 미세먼지 | 에어코리아 최종확정 측정자료 | 2016 ~ 2026-06 | `raw/weather/` | 분석용으로만 (공공누리 3유형) |
| 공휴일 | 한국천문연구원 특일 정보 API | 2016 ~ 2026 | `raw/calendar/` | 코드 아래 |
| 운영 대수·폐기·신규 | 서울시·서울시설공단 보도자료, 연보 | 연 단위 | `raw/external/fleet.csv` | 출처 열 필수 |
| 개입 이벤트 | 보도자료 | 날짜 | `raw/external/events.csv` | 출처 열 필수 |

모든 CSV는 01의 `to_utf8()`로 `data/interim/<폴더>_utf8/`에 UTF-8 사본을 만든 뒤 쓴다.

### ASOS 일자료 받기 (서울 108)

**방법 A — 기상자료개방포털(data.kma.go.kr, 로그인 필요)**

메뉴: 데이터 → 기상관측 → 지상 → 종관기상관측(ASOS) → 자료

| 설정 | 값 |
|---|---|
| 자료형태 | **일** |
| 기간 | 한 번에 최대 10년 → 두 번 받는다: ① 2015-01-01 ~ 2024-12-31 ② 2025-01-01 ~ 2026-06-30 |
| 지점 | 서울 → **서울(108)** |
| 요소 | 아래 13개 |
| 받기 | 조회 → CSV |

요소: **전체 선택을 권한다.** 서울 한 지점의 일자료라 전부 받아도 4천여 행 × 수십 열로 작다. 모형에 넣을 열은 1B에서 고르고, 나중에 다시 받을 일이 없어진다.

반드시 쓰는 13개: 기존 5개(평균기온, 일강수량, 평균 상대습도, 합계 일사량, 일 최심신적설) + 추가 8개(최고기온, 최저기온, 최소 상대습도, 합계 일조시간, 평균 풍속, 최대 풍속, 평균 지면온도, 1시간 최다강수량)

추가 후보 3개(메커니즘이 있어 EDA에서 확인):
- **강수 계속시간** — 젖은 노면에 있던 시간. 같은 강수량이라도 하루 종일 이슬비와 소나기는 다르다. '전날 비' 효과를 더 잘 잡을 수 있다
- **일 최심적설**(쌓인 눈 전체, 신적설과 다름) — 눈이 녹을 때까지 며칠 남는다. 제설제(염화칼슘)로 체인 부식이 빨라질 수 있다
- **최저 초상온도** — 지표면 근처 최저기온. 노면 결빙·서리의 대리 지표, 동결-융해 사이클 계산에 기온보다 맞다

받지 않아도 되는 것: 기압, 전운량(일조시간과 겹침), 지중온도, 증발량, 9-9 강수(일강수량과 겹침). 일교차는 최고−최저로 계산한다.

**방법 B — 공공데이터포털 API(코드로 한 번에, 재현 가능)**

공공데이터포털에서 '기상청_지상(종관, ASOS) 일자료 조회서비스'(15059093)를 활용신청한다(자동승인, 반영까지 최대 1~2시간). 키는 기존 `DATA_GO_KR_KEY` 그대로 쓴다.

```python
import os, requests
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
URL = "http://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"

rows = []
for y in range(2015, 2027):
    end = f"{y}1231" if y < 2026 else "20260630"
    r = requests.get(URL, params=dict(serviceKey=os.environ["DATA_GO_KR_KEY"], dataType="JSON",
                                      dataCd="ASOS", dateCd="DAY", startDt=f"{y}0101", endDt=end,
                                      stnIds="108", numOfRows=366, pageNo=1), timeout=60)
    items = r.json()["response"]["body"]["items"]["item"]
    rows += items
    print(y, len(items))                       # 평년 365, 윤년 366, 2026은 181

wx = pd.DataFrame(rows)                        # 모든 항목을 그대로 저장, 고르기는 1A-3에서
(ROOT / "data/raw/weather").mkdir(parents=True, exist_ok=True)
wx.to_csv(ROOT / "data/raw/weather/asos_daily_108_2015_202606.csv", index=False, encoding="utf-8")
wx.columns.tolist()
```

- API 응답은 영문 필드명이다(예: `avgTa` 평균기온, `maxTa`, `minTa`, `sumRn` 일강수량, `hr1MaxRn` 1시간 최다강수량, `avgWs` 평균 풍속). 1A-3에서 한글 이름과 맞춘다
- 방법 B로 받으면 이미 UTF-8이라 `to_utf8` 변환이 필요 없다

### 공휴일 수집

```python
import os, requests
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
URL = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo"

def get_holidays(year):
    out = []
    for m in range(1, 13):
        js = requests.get(URL, params=dict(serviceKey=os.environ["DATA_GO_KR_KEY"], solYear=year,
                                           solMonth=f"{m:02d}", _type="json", numOfRows=50), timeout=30).json()
        items = js["response"]["body"]["items"]
        if not items:                               # 그 달에 공휴일 없음 → 빈 문자열
            continue
        it = items["item"]
        it = [it] if isinstance(it, dict) else it   # 1건이면 리스트가 아니라 dict로 온다
        out += [{"date": pd.to_datetime(str(x["locdate"])), "name": x["dateName"]} for x in it]
    return out

holidays = pd.DataFrame([h for y in range(2016, 2027) for h in get_holidays(y)])
holidays.to_csv(ROOT / "data/raw/calendar/holidays_2016_2026.csv", index=False)
holidays.groupby(holidays.date.dt.year).size()
```

- [ ] 연도별 공휴일 수가 15~20일 안팎인지 확인한다(대체공휴일·임시공휴일 포함)
- 급하면 `pip install holidays`의 `holidays.KR()`로 대신할 수 있다. 단 임시공휴일이 빠질 수 있어 API 결과를 우선한다

### `events.csv` 초안 (날짜와 수치는 보도자료로 직접 확인해서 채운다)

```csv
event,start,end,value,unit,source,note
covid_distancing,2020-03-22,2022-04-17,,,질병관리청 보도자료,사회적 거리두기 기간(확인 필요). 단계 구분 없이 단일 더미
climate_card,2024-01-27,,,,서울시 보도자료,기후동행카드 시작(따릉이 포함 여부·날짜 확인)
family_pass,2025-04-23,,,,서울시 2025-04-22,가족권 도입
three_hour_pass,2025-11-13,,,,서울시 2025-11-12,3시간권 도입
chain_guard,,2025-12-31,16000,대(누적),서울시 2025-11-12,2030년까지 전체 적용. 시작 시점 확인 필요
top_tube_frame,,,32000,대(누적),서울시 2025-11-12,적용 완료 시점 확인 필요
```

`fleet.csv`에는 연도별 운영 대수(`date`는 연말, `fleet_n`), 폐기·신규 대수를 적는다. 2022~2025 폐기 2,105 / 4,500 / 1,400 / 2,000대, 신규 6,000 / 6,100 / 1,300 / 2,000대(서울시 팩트브리핑 2026-01-19), 2023년 이후 운영 대수 약 45,000대. 그 이전 연도는 연보·보도자료로 채운다. 단계 2가 끝나면 '월별 실제 운행 자전거 수'로 바꿀 수 있다.

---

### 1A-2 마지막: UTF-8 사본 만들기

받은 원본은 `data/raw/`에 그대로 두고, 읽을 파일은 `data/interim/<폴더>_utf8/`에 모은다. 1A-3의 코드는 모두 이 `_utf8` 폴더를 읽는다.

```python
import shutil
from prep import make_utf8_folder

# 일별 대여건수, 이용정보(일별): CSV는 UTF-8 사본, 엑셀은 복사
for folder in ["rent_daily", "use_daily"]:
    print(folder)
    display(make_utf8_folder(ROOT / "data/raw" / folder, ROOT / f"data/interim/{folder}_utf8"))

# 고장신고: CSV 13개는 단계 0에서 이미 변환했다. 2015 ~ 2020.10 엑셀만 복사한다
for f in (ROOT / "data/raw/failure").glob("*.xlsx"):
    shutil.copy(f, ROOT / "data/interim/failure_utf8" / f.name)
    print("복사:", f.name)
```

- 폴더 이름(`rent_daily`, `use_daily`)이 실제와 다르면 `print(sorted(p.name for p in (ROOT / "data/raw").iterdir()))`로 확인하고 고친다
- 고장신고에 `make_utf8_folder`를 다시 돌리지 않는다. `22.07-12`(이름만 .csv인 엑셀)가 .xlsx로 한 번 더 복사돼 같은 기간이 두 번 들어간다
- [ ] 각 표의 `how`가 cp949 / utf-8-sig / 엑셀 복사 중 하나인지

**일별 대여건수 파일 점검** — 해마다 파일 이름·열 이름이 다르므로, 파일마다 날짜 범위와 한 날짜당 행 수를 보고 겹치거나 빈 기간이 없는지 확인한다.

```python
rows = []
for f in data_files(ROOT / "data/interim/rent_daily_utf8"):
    x = read_any(f)
    dcol = next((c for c in x.columns if "일자" in c or "일시" in c or c in ("날짜", "일")), None)
    ncol = next((c for c in x.columns if "건수" in c), None)
    dt = pd.to_datetime(x[dcol].astype(str).str[:10], errors="coerce", format="mixed") if dcol else pd.Series(dtype="datetime64[ns]")
    n = pd.to_numeric(x[ncol].astype(str).str.replace(",", ""), errors="coerce") if ncol else pd.Series(dtype=float)
    rows.append({"file": f.name, "rows": len(x), "date_col": dcol, "count_col": ncol,
                 "first": dt.min(), "last": dt.max(), "n_dates": dt.nunique(),
                 "missing_in_file": (dt.max() - dt.min()).days + 1 - dt.nunique() if dcol else None,
                 "rows_per_date": round(len(x) / max(dt.nunique(), 1), 1),
                 "total": n.sum(), "cols": " | ".join(x.columns)})
chk = pd.DataFrame(rows).sort_values(["first", "file"]).reset_index(drop=True)
chk["gap_days"] = (chk["first"] - chk["last"].shift()).dt.days - 1
pd.set_option("display.max_colwidth", 60)
chk.drop(columns="cols")
```

| 열 | 정상 | 이상 |
|---|---|---|
| `rows_per_date` | 1 (하루 한 줄 = 일별 합계) | 수백 → 대여소×일 파일. 날짜별로 더해야 한다 |
| `gap_days` | 0 (앞 파일 다음 날부터 시작) | 양수 = 빠진 날, 음수 = 겹친 기간 |
| `missing_in_file` | 0 | 파일 안에서 빠진 날 |
| `date_col` / `count_col` | 이름이 잡힘 | None → `chk.cols`로 실제 열 이름을 보고 후보에 추가 |

- `[결정]` 대여소별 **반납내역**은 같은 이용을 반납 쪽에서 센 것이라 대여내역과 기간이 겹친다(`gap_days` 음수). 같은 해 대여·반납 `total`이 거의 같으면 확인 끝 → 반납내역은 읽지 않는다
- [ ] 2016-01-01 ~ 2026-06-30이 빈 날 없이 이어지는지 기록

**이용정보(일별) 파일 목록 점검** — 파일이 크므로(월 100~200MB) 내용 대신 이름으로 끝 기간을 확인한다.

```python
names = [f.name for f in data_files(ROOT / "data/interim/use_daily_utf8")]
print(len(names), "개")
print(*names[-8:], sep="\n")
```

---

## 1A-3. 전처리 (1~2일) — `notebooks/1A_build_master.ipynb`

> **결과(2026-10-05) — 아래 절차와 다르게 한 것.** 이유와 근거 수치는 `docs/notes/01_데이터전처리_메모.md`, `docs/decision_log.md`.
> - 노트북 이름은 `1A_Build_master.ipynb`(대문자 B).
> - 고장신고: 파일에서 통째로 빠진 6일을 API로 채웠다(`py/build/failure_gaps.py`). 아래 "반기 경계일 보간"은 하지 않았다.
> - 대여건수: `read_rent_daily`에 쉼표 제거와 빈 줄 제거를 넣었고, 2015~2017 반납내역은 읽지 않는다.
> - 이용정보: 아래 `read_use`(열 이름 기준)로는 읽히지 않는다(헤더가 10가지). `py/build/use_daily.py`가 열 위치로 읽어 날짜별 합계를 만든다. 구멍 난 564일은 `py/build/rent_history_daily.py`가 대여이력에서 만든 값으로 바꾼다.
> - 이동거리: `dist_km_raw`(기록 그대로), `dist_zero_share`, `dist_ok`, `dist_km`(dist_ok인 날만)으로 나눴다.
> - 운영이 멈춘 날: `no_service`(3일), `partial_service`(10일), 신고만 적은 기간 `report_anomaly`(2023-09-07 ~ 10-10)를 표시했다. 2019-09-07은 보간하지 않고 0.
> - 운영 대수: `fleet.csv` 보간 대신 `bikes_active`, `bikes_active_prev`(그 달·전달에 대여된 자전거번호 수, `py/build/bike_month.py`).
> - 코로나·개입 더미(`covid` 등)는 아직 넣지 않았다. 날짜·출처는 `data/raw/external/events.csv`에 있고, 무엇을 넣을지는 체크포인트 B에서 정한다.

### 1차 대비 바꾸는 규칙

| 항목 | 1차 | 이번 |
|---|---|---|
| 고장 유형 | 단말기·파손·잠금장치를 기타에 합침 | **전 유형을 따로 유지**. 분석 대상은 타이어·체인·안장·페달 + 전체. 단말기는 별도 열 |
| 건수 기준 | 행 수 | 세 가지를 모두 보관한다(단계 0 결정): ① **행 수**(신고×유형, 공표치와 같은 기준) ② **신고 건수**(고유 자전거+등록일시, **전체 고장의 주 타깃**) ③ **중복 병합 건수**(같은 자전거·유형 48시간 안 = 1건, 민감도 분석). 유형별 집계는 행 수 |
| 강수 | 0~3 범주(IQR) | **mm 그대로** + `log1p`. 범주는 EDA 표에서만 |
| 적설 | 0/1 | cm 그대로 + 유무 |
| 결측 | 0으로 치환 | 강수·적설 빈칸은 0이 맞다(관측상 없음). **기온·습도·일사 빈칸은 진짜 결측** → 보간하고 `wx_imputed` 표시 |
| 반기 경계일 | 2021-06-30, 2021-12-31, 2022-12-31을 전후 평균으로 보간 | 새 파일로 먼저 확인. 여전히 비정상이면 파일 누락으로 보고 보간 + 표시 |

### 공통 도우미 — `py/prep.py`

`py/prep.py` 전체 내용이다. 인코딩 함수(01 문서 0-4)와 열 이름·파일 읽기 함수를 한 파일에 둔다. **.py 파일은 노트북과 따로 실행되므로 맨 위 import 줄이 꼭 있어야 한다.** 05(대여이력 적재)에서도 `from prep import to_utf8`로 다시 쓴다.

```python
"""따릉이 프로젝트 공통 도우미 — 인코딩 변환, 열 이름 표준화, 파일 읽기

노트북에서:  import sys; sys.path.append("../py");  from prep import to_utf8, std_name, read_any
"""
import codecs
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# 인코딩 변환 (01 문서 0-4)
# ---------------------------------------------------------------------------

def sniff(path, n=200_000):
    """파일 앞부분으로 형식·인코딩을 추정한다"""
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
    return "mixed"                                 # 대부분은 맞는데 깨진 바이트가 섞여 있다


def _best_guess(path, n=5_000_000):
    """깨진 바이트가 섞인 파일: utf-8과 cp949 중 대체 글자가 적은 쪽을 고른다"""
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
    """폴더 안의 CSV·엑셀만 이름순으로 (맥의 .DS_Store 같은 숨김 파일 제외)"""
    return sorted(p for p in Path(folder).iterdir()
                  if p.suffix.lower() in (".csv", ".xlsx", ".xls") and not p.name.startswith((".", "~$")))


def make_utf8_folder(src_dir, dst_dir):
    """raw 폴더 → interim/<폴더>_utf8. CSV는 UTF-8 사본, 엑셀은 그대로 복사(이름만 .csv인 엑셀은 .xlsx로)"""
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
```

### 고장신고 읽기와 정리

```python
from prep import std_name, read_any, data_files

def read_fail(path):
    x = read_any(path)
    x = std_name(x, "bike_no", ["자전거번호"])
    x = std_name(x, "reg_dt", ["등록일시"])
    x = std_name(x, "kind", ["고장구분", "구분"])
    return x[["bike_no", "reg_dt", "kind"]].assign(src=Path(path).name)

files = data_files(ROOT / "data/interim/failure_utf8")          # CSV·엑셀만 (.DS_Store 제외)
fail = pd.concat([read_fail(f) for f in files], ignore_index=True)
fail["bike"]   = fail.bike_no.str.extract(r"(\d+)", expand=False).astype("Int64")
fail["reg_ts"] = pd.to_datetime(fail.reg_dt, format="mixed", errors="coerce")   # 한국 시간 그대로(시간대 없음)
fail["date"]   = fail.reg_ts.dt.normalize()
fail["kind"]   = fail.kind.str.replace(r"\s+", " ", regex=True).str.strip()

fail.kind.value_counts()                                  # 고장구분 실제 값 목록부터 확인
fail.reg_ts.isna().sum(), fail.bike.isna().sum()          # 파싱 실패 수
fail.groupby(fail.date.dt.year).size()                  # 2015 ~ 2026이 모두 있는지(엑셀 포함 확인)
```

- [ ] `kind` 값 목록을 기록한다(예: 타이어, 체인, 안장, 페달, 단말기, 잠금장치 불량, 파손, 기타). 띄어쓰기 변형이 있으면 통일한다
- [ ] 날짜 파싱 실패와 자전거번호 결측이 0에 가까운지

### 중복 병합 표시

```python
# 단계 0 확인: 2021-02 ~ 2021-12 파일만 날짜만 있다(나머지는 시각 있음) → 파일 단위가 아니라 행 단위로 판단
fail["has_time"] = fail.reg_dt.str.contains(":", na=False)

fail = fail.sort_values(["bike", "kind", "reg_ts"])
gap_h = fail.groupby(["bike", "kind"]).reg_ts.diff().dt.total_seconds() / 3600
dedup_h = np.where(fail.has_time, 48, 24)          # 날짜만 있으면 '같은 날·다음 날'
fail["keep"] = gap_h.isna() | (gap_h > dedup_h)

1 - fail.keep.mean()        # 병합으로 빠지는 비율 → 결정 로그에 기록
```

### 일별 유형별 집계

```python
kind_map = {"타이어": "tire", "체인": "chain", "안장": "saddle", "페달": "pedal",
            "단말기": "terminal", "잠금장치 불량": "lock", "파손": "broken", "기타": "etc"}

def to_daily(df, prefix):
    k = df.kind.map(kind_map).fillna("etc")
    return (df.assign(k=k).groupby(["date", "k"]).size()
              .unstack(fill_value=0).add_prefix(prefix).reset_index())

daily_fail  = to_daily(fail, "fail_")
daily_faild = to_daily(fail[fail.keep], "faild_")     # 중복 병합 버전
```

한 번 신고에 여러 유형을 고르면 같은 자전거·같은 일시로 여러 행이 생긴다(단계 0 Q1에서 API로 확인). 유형별 집계는 행 수 그대로 쓰고, **전체 신고 건수**는 고유 (자전거, 일시)로 따로 센다. 둘을 섞지 않는다.

```python
daily_report = (fail.drop_duplicates(["bike", "reg_ts"]).groupby("date").size()
                    .rename("reports").reset_index())       # 전체 신고 건수(주 타깃 후보)
```

- 2021-02 ~ 2021-12는 시각이 없어 같은 날 같은 자전거의 신고가 여러 번이어도 1건으로 묶인다 → 이 구간의 `reports`는 실제보다 약간 적다. 결정 로그에 적고, 모형에 `no_time` 더미(2021년)를 넣거나 2022년부터 학습하는 방안을 03에서 비교한다
- 공식 연간 건수는 **행 기준**이다(단계 0: 2024년 파일 행 161,846 ≈ 공표 162,058, 2025년 112,879 ≈ 112,970). 보고서에서 공표치와 비교할 때는 행 수를 쓴다
- [ ] 1차 분석의 '전체 고장 건수'가 행 합계였는지 신고 건수였는지 확인해 결정 로그에 적는다. 파일에서 한 신고가 여러 행으로 나뉘는지(시각까지 같은 행이 있는지)도 확인한다

### 노출 3종

- **대여 건수** `rentals`: 1차와 같은 파일을 읽어 일자별로 합산한다. 컬럼 이름이 해마다 달랐다(`대여일자`/`대여일시`/`대여 일자 / 월`, `대여건수`/`대여 건수`).

```python
def read_rent_daily(path):
    x = read_any(path)
    x = std_name(x, "date_raw", ["대여일자", "대여일시", "대여 일자 / 월"])
    x = std_name(x, "n", ["대여건수", "대여 건수"])
    return pd.DataFrame({"date": pd.to_datetime(x.date_raw.str[:10], errors="coerce"),
                         "rentals": pd.to_numeric(x.n, errors="coerce")})

rent_daily = (pd.concat([read_rent_daily(f) for f in data_files(ROOT / "data/interim/rent_daily_utf8")])
                .groupby("date", as_index=False).rentals.sum())
```

- **이동거리** `dist_km`, **이용시간** `use_hours`: OA-15246(대여소×일)을 일자별로 합산한다.

```python
def read_use(path):
    x = read_any(path)
    for new, cands in {"date_raw": ["대여일자", "대여일"], "n_use": ["이용건수"],
                       "dist_m": ["이동거리(M)", "이동거리"], "min_use": ["이용시간(분)", "이용시간"]}.items():
        x = std_name(x, new, cands)
    return pd.DataFrame({"date": pd.to_datetime(x.date_raw.str[:10], format="mixed", errors="coerce"),
                         **{c: pd.to_numeric(x[c], errors="coerce") for c in ("n_use", "dist_m", "min_use")}})

use_daily = (pd.concat([read_use(f) for f in data_files(ROOT / "data/interim/use_daily_utf8")])
               .groupby("date").agg(n_use=("n_use", "sum"), dist_m=("dist_m", "sum"), min_use=("min_use", "sum")))
use_daily = use_daily.assign(dist_km=use_daily.dist_m / 1000, use_hours=use_daily.min_use / 60)[
    ["n_use", "dist_km", "use_hours"]].reset_index()
```

- [ ] 컬럼 이름과 단위(이용시간이 분인지)를 실제 파일에서 확인한다
- [ ] `n_use` 일합계가 `rentals`와 거의 같은지(차이 1% 이내 기대). 크게 다르면 두 출처의 정의 차이를 기록한다

### ASOS 일자료 읽기

방법 B(API)로 받은 파일을 읽는다. 열 이름이 영문 약어라 아래 표로 표준 이름을 붙인다(2026-10-01 받은 62열 기준). 방법 A(포털 CSV)로 받았다면 한글 열 이름을 같은 표준 이름으로 맞춘다.

| API 열 | 표준 이름 | 뜻 | 빈칸 처리 |
|---|---|---|---|
| `tm` | date | 날짜 | |
| `avgTa` / `maxTa` / `minTa` | tavg / tmax / tmin | 평균·최고·최저기온(℃) | 보간 |
| `sumRn` | rain_mm | 일강수량(mm) | 0 |
| `hr1MaxRn` | rain_1h_max | 1시간 최다강수량(mm) | 0 |
| `sumRnDur` | rain_dur_h | 강수 계속시간(hr) | 0 |
| `avgRhm` / `minRhm` | humidity / hum_min | 평균·최소 상대습도(%) | 보간 |
| `sumGsr` | solar | 합계 일사량(MJ/m²) | 보간 |
| `sumSsHr` | sunshine | 합계 일조시간(hr) | 보간 |
| `avgWs` / `maxWs` | wind_avg / wind_max | 평균·최대 풍속(m/s) | 보간 |
| `avgTs` | ground_temp | 평균 지면온도(℃) | 보간 |
| `minTg` | grass_tmin | 최저 초상온도(℃) | 보간 |
| `ddMefs` | snow_cm | 일 최심신적설(cm, 그날 새로 온 눈) | 0 |
| `ddMes` | snow_depth_cm | 일 최심적설(cm, 쌓인 눈 전체) | 0 |

```python
ASOS_API = {"tm": "date",
            "avgTa": "tavg", "maxTa": "tmax", "minTa": "tmin",
            "sumRn": "rain_mm", "hr1MaxRn": "rain_1h_max", "sumRnDur": "rain_dur_h",
            "avgRhm": "humidity", "minRhm": "hum_min",
            "sumGsr": "solar", "sumSsHr": "sunshine",
            "avgWs": "wind_avg", "maxWs": "wind_max",
            "avgTs": "ground_temp", "minTg": "grass_tmin",
            "ddMefs": "snow_cm", "ddMes": "snow_depth_cm"}

raw = pd.read_csv(ROOT / "data/raw/weather/asos_daily_108_2015_202606.csv", dtype=str)
asos = raw[list(ASOS_API)].rename(columns=ASOS_API)
asos["date"] = pd.to_datetime(asos["date"])
num = asos.columns.drop("date")
asos[num] = asos[num].apply(pd.to_numeric, errors="coerce")      # 빈 문자열 → NaN

print(len(asos), asos.date.min(), asos.date.max())                 # 4,199행, 2015-01-01 ~ 2026-06-30
asos.isna().mean().round(3)                                        # 열별 빈칸 비율
```

- [ ] 강수·적설 열은 빈칸이 많다(비·눈이 없던 날) → 정상. 합칠 때 0으로 채운다
- [ ] 기온·습도·일사 열의 빈칸은 진짜 결측이다. 비율을 기록한다(보통 1% 미만)
- [ ] 받지 않아도 되는 열(기압, 전운량, 지중온도, 증발량 등)은 여기서 버린다. 원본 CSV에는 모두 남아 있다

### 합치기와 파생변수

```python
m = pd.DataFrame({"date": pd.date_range("2016-01-01", "2026-06-30")})        # 3,834일
for part in (rent_daily, use_daily, asos, daily_fail, daily_faild, daily_report):
    m = m.merge(part, on="date", how="left")

fail_cols = [c for c in m if c.startswith(("fail_", "faild_"))]
m[fail_cols] = m[fail_cols].fillna(0).astype(int)
zero_cols = ["rain_mm", "rain_1h_max", "rain_dur_h", "snow_cm", "snow_depth_cm"]
m[zero_cols] = m[zero_cols].fillna(0)                                       # 빈칸 = 관측상 없음

wx_cols = ["tavg", "tmax", "tmin", "humidity", "hum_min", "solar", "sunshine", "wind_avg", "wind_max",
           "ground_temp", "grass_tmin"]
m["wx_imputed"] = m[wx_cols].isna().any(axis=1)
m[wx_cols] = m[wx_cols].interpolate(limit_direction="both")                 # 기온 등은 선형 보간

hol = pd.read_csv(ROOT / "data/raw/calendar/holidays_2016_2026.csv", parse_dates=["date"])

m["total_rows"] = m.filter(regex=r"^fail_").sum(axis=1)                    # 행(신고×유형) 합계 = 공표 기준. faild_는 섞이지 않는다
m["reports"] = m["reports"].fillna(0).astype(int)
m["total_faults"] = m["reports"]                                             # 전체 고장의 주 타깃 = 신고 건수(단계 0 결정). 03·04는 이 열을 쓴다
m["year"], m["month"], m["doy"] = m.date.dt.year, m.date.dt.month, m.date.dt.dayofyear
m["dow"] = m.date.dt.dayofweek + 1                                           # 1 = 월 … 7 = 일
m["is_weekend"] = m.dow >= 6
m["is_holiday"] = m.date.isin(hol.date)
m["is_off"] = m.is_weekend | m.is_holiday
prev_off = m.is_off.shift(1, fill_value=False)
next_off = m.is_off.shift(-1, fill_value=False)
m["after_off"] = ~m.is_off & prev_off                                        # 쉬는 날 다음 첫 근무일(월요일 포함)
m["bridge"] = ~m.is_off & prev_off & next_off                                # 징검다리 근무일
m["rain_any"] = m.rain_mm >= 1
m["rain_lag1"], m["rain_lag2"] = m.rain_mm.shift(1), m.rain_mm.shift(2)
m["days_since_rain"] = m.groupby(m.rain_any.cumsum()).cumcount().clip(upper=14)
m["temp_range"] = m.tmax - m.tmin
m["freeze_thaw"] = (m.tmin < 0) & (m.tmax > 0)
m["hot"], m["cold"] = m.tmax >= 30, m.tmin <= -5                             # 기준값은 EDA 뒤 조정
for k in (1, 2):
    m[f"fs{k}"] = np.sin(2 * np.pi * k * m.doy / 365.25)
    m[f"fc{k}"] = np.cos(2 * np.pi * k * m.doy / 365.25)
m["covid"] = m.date.between("2020-03-22", "2022-04-17")                     # events.csv 확인값으로 교체
```

### 운영 대수·개입 변수 (연 단위 값 → 일 단위 보간)

```python
fleet = pd.read_csv(ROOT / "data/raw/external/fleet.csv", parse_dates=["date"])   # date(연말), fleet_n
m = m.merge(fleet[["date", "fleet_n"]], on="date", how="left")
m["fleet_n"] = m.fleet_n.interpolate().ffill().bfill()
```

체인가드처럼 월별 경로를 모르는 개입은 두 버전을 만든다.
- 버전 A: 시작일 이후 더미
- 버전 B: 알려진 누적 대수 사이를 선형 보간한 비율

모형에 넣고 결과가 두 버전에서 같은 방향인지 본다(민감도).

### 저장

```python
m.to_parquet(ROOT / "data/processed/daily_master_v2.parquet", index=False)
m.to_csv(ROOT / "data/processed/daily_master_v2.csv", index=False)
fail[["bike", "reg_ts", "kind", "keep"]].to_parquet(ROOT / "data/processed/fail_std.parquet", index=False)   # 단계 2에서 재사용
```

---

## 1A-4. 데이터 검증 (반나절)

아래를 표로 모아 `output/tables/validation.csv`로 저장한다.

- [ ] 행 수 = 3,834 (2016-01-01 ~ 2026-06-30), 날짜 중복 0
- [ ] 열별 결측 수(`m.isna().sum()`). 대여 건수 결측은 2019-09-07 하루뿐이어야 한다(1차 파일 기준). 새로 생기면 원인 기록
- [ ] **연도별 고장 합계 vs 공표값**: 2024년 161,846(열린데이터 파일) / 162,058(공단 발표), 2025년 112,879 / 112,970. 차이 0.5% 이내 기대. 공표값은 행 기준이므로 `total_rows`로 비교한다

```python
m.groupby("year")[["total_rows", "total_faults"]].sum()      # total_faults(신고 건수)는 행보다 약 12% 적어야 한다
```

- [ ] 1차 `dareungi_master.csv`와 2016~2024 겹치는 구간 비교: 날짜별 차이 목록 → 원인(파일 재등록, 유형 처리 변경 등) 기록
- [ ] 반기 경계일(06-30, 12-31)의 건수가 0이거나 급감하지 않는지
- [ ] 노출 3종의 상관(`m[["rentals","dist_km","use_hours"]].corr()`): 0.95 이상이면 어느 것을 써도 결과가 비슷하다. 선택은 03 문서에서
- [ ] `wx_imputed` 일수 기록

---

## 1A-5. 레짐 점검 — 체크포인트 A (반나절) `[결정]`

> **결과(2026-10-05).** 분석은 `notebooks/1A_regime.ipynb`(별도 노트북), 요약은 `docs/notes/02_EDA_메모.md`.
> - 신고율 수준 변화: 2018-06-20 2배, 2020-05 ~ 2021-03 절반, 2021-04-05~08 2배, 2023-09-07 ~ 10-10 약 −40%, 2025-03부터 약 −20%(체인 0.68배·안장 0.72배, 2026년 상반기까지 유지).
> - 아래 절차에 없는 분석을 추가했다: 자전거 도입 시기 묶음별 신고율(구성 효과인지 같은 자전거의 변화인지 구분), 외부 자료 조사와 검증(`docs/reference/07~10`).
> - 학습 구간 후보가 달라졌다: 아래 분할(Train 2020 ~ 2023)의 2020-05 ~ 2021-03은 신고율이 절반인 기간이다. 후보는 2021-04부터 / 2022-03부터(QR 전수 교체 완료 뒤) / 2020-01 + 더미.
> - **판정(2026-10-05)**: 다른 레짐이다 → 수준 적응 변수 + 테스트 기간별 보고. 학습 시작은 2021-04-08을 기본으로 하고 1B에서 2022-03과 비교. EDA 구간도 2021-04-08 ~ 2024-12-31로 맞춘다.

테스트 구간(2025~)은 **월별 수준만** 본다. 요일·기상 패턴 분석에는 쓰지 않는다.

```python
cols = ["fail_tire", "fail_chain", "fail_saddle", "fail_pedal", "total_faults", "rentals", "dist_km"]
monthly = m.groupby(m.date.dt.to_period("M"))[cols].sum()
monthly["rate"] = monthly.total_faults / monthly.rentals * 1e4
monthly["rate"].plot(figsize=(10, 4), title="대여 만 건당 고장신고(월별)")

import ruptures as rpt                                   # 구조변화 시점 찾기
m20 = monthly.loc["2020-01":]
algo = rpt.Binseg(model="l2", min_size=6).fit(m20.rate.to_numpy())
for n in (1, 2, 3):
    bk = algo.predict(n_bkps=n)[:-1]                     # 마지막 값은 끝 지점이라 뺀다
    print(n, [str(m20.index[b]) for b in bk])
```

그림과 함께 보고, 시점이 그림에서 납득되는 개수를 고른다.

부품별로 감소가 어디에 몰렸는지 본다.

```python
yr = m[m.year.between(2023, 2025)].groupby("year")
by_year = yr[["fail_tire", "fail_chain", "fail_saddle", "fail_pedal", "fail_terminal", "total_faults"]].sum() \
            .div(yr.rentals.sum(), axis=0) * 1e4
by_year.loc[2025] / by_year.loc[2024]          # 부품별 2025/2024 비율
```

해석 가이드

| 관찰 | 가능한 원인 | 모형에 반영 |
|---|---|---|
| 전 부품이 비슷한 비율로 감소 | 신고 경로·집계 방식 변화, 이용자 구성 변화 | 수준 적응 변수(최근 4주 고장률) 필수 |
| 체인에 집중 | 체인가드 설치 효과 | 체인가드 개입 변수 |
| 타이어·안장에 집중 | 프레임·부품 교체, 정비 강화 | 해당 개입 변수, 한계 절에 서술 |
| 특정 시점에 계단식 | 정책·시스템 변경일 | 그 날짜 더미, 보도자료로 원인 확인 |

판정
- [ ] 2025~2026이 2023~2024와 같은 레짐인가?
  - 같다 → 00 문서의 분할 그대로
  - 다르다 → ① 04 문서에서 수준 적응 변수를 모든 모형에 넣고 ② 테스트 결과를 2025 상반기·하반기·2026 상반기로 나눠 보고 ③ 원인 조사 결과를 한계 절에 쓴다
- [ ] 결정 로그에 기록한다

---

## 1A-6. 고장률 EDA (2~3일) — `notebooks/1A_eda.ipynb`

> **결과(2026-10-05).** 요약은 `docs/notes/02_EDA_메모.md`.
> - 분석 구간은 아래의 2021-01-01이 아니라 **2021-04-08 ~ 2024-12-31**(체크포인트 A).
> - 아래 표의 방법(그룹별 신고율)에 더해 "그 무렵 수준 대비"(그날 신고율 ÷ 앞뒤 2주 중앙값)를 썼다. 계절·추세를 걷어 내고 날씨·요일 효과를 보기 위해서다.
> - 재현 기준과 다른 점: 기온 U자형은 한파 효과만 남았고(더위는 계절 효과), 전날 비 효과는 +2 ~ 5%로 작다. 요일 패턴과 부품별 계절 진폭은 재현됐다.
> - 7번(미세먼지 구간별)은 미세먼지 자료를 받지 않아 하지 않았다.

분석 구간은 **2021-01-01 ~ 2024-12-31**. 2020년은 코로나 기간이라 제외하고, 2025년 이후는 테스트라 제외한다.

```python
m = pd.read_parquet(ROOT / "data/processed/daily_master_v2.parquet")
eda = m[m.date.between("2021-01-01", "2024-12-31")].copy()
parts = ["fail_tire", "fail_chain", "fail_saddle", "fail_pedal", "total_faults"]

def rate_by(df, key):
    """그룹별 '대여 만 건당 신고' (합계 ÷ 합계)"""
    g = df.groupby(key, observed=True)
    out = g[parts].sum().div(g["rentals"].sum(), axis=0) * 1e4
    out.insert(0, "days", g.size())
    return out.round(1)

def save_tab(x, name):
    x.to_csv(ROOT / f"output/tables/eda_{name}.csv")
```

### 분석 목록과 재현 기준

재현 기준은 1차 `dareungi_master.csv`(2021~2024)로 위 `rate_by()`를 돌려 미리 계산해 둔 값이다. 새 데이터에서는 조금 달라질 수 있다. 크게 다르면 파일 재등록·유형 처리 차이를 의심한다.

| # | 분석 | 코드 | 재현 기준 |
|---|---|---|---|
| 1 | 기술통계표(1차 표 3 재생성) | `eda[cols].describe().T` + 왜도 `eda[cols].skew()` | 1차 표 3과 다른 값은 원인 기록 |
| 2 | 상관 4버전 | 아래 | 수준 0.89 / 1차 차분 0.91 / 연도 내 0.87~0.96 / 고장률 0.20 |
| 3 | 요일별 고장률 | `rate_by(eda, "dow")` | 월 45.3, 화 43.6, 수 43.6, 목 42.6, 금 40.7, 토 35.1, 일 36.0 |
| 4 | 월별 고장률 지수(부품 평균 = 100) | 아래 | 타이어 2월 60 / 8월 161, 안장은 진폭이 가장 작음 |
| 5 | 기온 2℃ 구간별 | `rate_by(eda.assign(tbin=pd.cut(eda.tavg, np.arange(-16, 36, 2))), "tbin")` | U자형: 0℃ 이하 49.9, 28℃ 이상 43.1, 전체 41.3 |
| 6 | 전날 강수 효과(당일 무강수 날만) | 아래 | 전날 비가 온 날 +10% 안팎(1차 범주 기준 41.2 → 45.5) |
| 7 | 일교차·동결융해·지면온도·미세먼지 구간별 | 5번과 같은 방식 | 새 분석 |
| 8 | 반기별 고장률 추이 | 아래 | 2021H2 52.2 → 2024H1 35.7 |

```python
# 2. 상관 4버전
eda["rate"] = eda.total_faults / eda.rentals * 1e4
print({"level": eda.total_faults.corr(eda.rentals),
       "diff1": eda.total_faults.diff().corr(eda.rentals.diff()),
       "rate":  eda.rate.corr(eda.rentals)})
print(eda.groupby("year").apply(lambda g: g.total_faults.corr(g.rentals)))

# 4. 월별 지수
mi = rate_by(eda, "month")[parts]
save_tab((100 * mi / mi.mean()).round(0), "month_index")

# 6. 전날 강수 효과
d6 = eda.assign(rain_y=pd.cut(eda.rain_lag1, [-np.inf, 0, 5, 20, np.inf], labels=["없음", "약", "보통", "강"]))
save_tab(rate_by(d6[d6.rain_mm == 0], "rain_y"), "rain_lag1")

# 8. 반기별
save_tab(rate_by(eda.assign(h=eda.year.astype(str) + np.where(eda.month <= 6, "H1", "H2")), "h"), "half_year")
```

그림은 `plt.savefig(ROOT / "output/figures/eda_XX.png", dpi=200, bbox_inches="tight")`로 저장한다.

확인
- [ ] 재현 기준과 대체로 같은가. 다르면 이유를 적는다
- [ ] 부품마다 계절 진폭이 다른가(타이어 큼, 안장 작음)
- [ ] 노출 통제 뒤에도 남는 패턴 목록을 만든다

---

## 1A-7. 변수 목록 확정 — 체크포인트 B `[결정]`

> **결과(2026-10-05).** `docs/variables_v1.md`로 확정. 아래 표와 다른 점: 평균기온 스플라인 대신 한파 변수 두 형태(`cold_wave`, `winter_anom`)를 1B에서 비교, 더위·동결융해·평균기온·징검다리·눈은 EDA에서 뺄 가능성이 높게 나왔지만 일단 넣고 모형 성능으로 판단, 개입 변수는 유연한 예측 모형에서 넣은 것·뺀 것을 비교, 신고 누락 기간은 학습에서 제외.

아래 표를 채워 `docs/variables_v1.md`로 저장한다. **여기서 목록을 닫아야** 03·04가 깔끔해진다.

| 변수 | 근거(EDA 수치) | 형태 | 예측 시점에 미래 값을 아는가 | 쓰는 곳 |
|---|---|---|---|---|
| 요일, 공휴일, 쉬는 날 다음날, 징검다리 | 요일별 고장률 차이 | 더미 | 안다 | 03·04 |
| 계절(fs1·fc1·fs2·fc2) | 월별 지수 | 사인·코사인 | 안다 | 03·04 |
| 평균기온 | U자형 | 스플라인(자유도 4) 또는 구간 | 예보로 안다 | 03·04 |
| 강수(당일·전날) | 전날 효과 | log1p(mm) | 당일은 예보, 전날은 안다 | 03·04 |
| 일교차, 동결융해 | 표 7 | 연속·더미 | 예보로 안다 | 03·04 |
| 코로나, 개입 이벤트 | 레짐 점검 | 더미·비율 | 안다(과거 사건) | 03·04 |
| 운영 대수 | 연 단위 | 연속 | 거의 안다 | 03 |
| 노출(건수·거리·시간 중 하나) | 03에서 결정 | offset | **모른다 → 따로 예측해야 한다** | 03 offset, 04 노출 예측 |
| 최근 4주 고장률(수준) | 레짐 변화 | 연속 | 안다(과거 값) | 04 |
| 전일·7일 이동평균 고장 건수 | 예측력 있음 | 연속 | D+1만 안다. **D+7 이상 예측에는 7일 이상 밀어서만 사용** | 04 |

판단 기준(모든 후보에 적용): "노출의 또 다른 얼굴인가(누적 대여량, 대여량 이동평균 → 제외), 노출 통제 후에도 남는 위험 요인인가(요일, 전날 비, 기온 U자 → 포함)?"

- [ ] 표를 채우고 결정 로그에 "변수 목록 v1 확정"을 기록한다

---

## 흔한 함정

- **대여량을 설명변수로 넣는다** → 노출이다. offset(03)이나 별도 예측(04)으로만 쓴다
- **2025년 이후 패턴을 보고 변수를 고른다** → 테스트 오염. 레짐 판단용 월별 수준만 본다
- **강수 빈칸과 기온 빈칸을 똑같이 0으로 채운다** → 강수만 0이 맞다
- **공휴일 API의 '1건짜리 달'** → 리스트가 아니라 dict로 온다. 위 코드처럼 감싼다
- **시간대(tz)를 붙인다** → 모든 날짜·시각은 한국 시간 그대로, 시간대 정보 없이 쓴다. 저장할 때도 마찬가지
- **노트북에서 셀 순서를 뒤섞어 실행한다** → 마지막에 '커널 재시작 후 전체 실행'이 한 번에 통과해야 재현된다
- **스크립트 밖에서 엑셀로 고친다** → 재현이 깨진다. 고칠 일이 있으면 코드에 규칙으로 넣는다

## 완료 기준

- [ ] `daily_master_v2.parquet` 3,834행, 결측·표시가 기록돼 있다
- [ ] `validation.csv`에서 연도별 합계가 공표값과 맞다
- [ ] 체크포인트 A(레짐) 판정이 결정 로그에 있다
- [ ] EDA 표 8개와 그림이 코드로 저장돼 있다
- [ ] `variables_v1.md`(체크포인트 B)가 있다
- [ ] 노트북 두 개가 '커널 재시작 후 전체 실행'으로 끝까지 돈다
