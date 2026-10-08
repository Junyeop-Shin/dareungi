"""대여이력 공백 채우기 — 반기 파일(~2026-06-30)과 API 수집 시작 사이를 API로 받는다

[무엇을] 시간대 목록을 만들어(rent_hours_done에 있는 것은 건너뜀) API 호출은 4개 스레드로 동시에, DB 저장은 잠금 안에서 순서대로 한다.
         시간대당 호출 10 ~ 15회(1,000건씩)라 직렬로는 시간대당 약 1분 → 100일에 40시간. 4개 동시면 약 10시간.
         한 묶음(8시간대)마다 잠금을 풀고 3초 쉬어 다른 작업(수집·갱신)이 끼어들 수 있게 한다.
[실행]   PYTHONUNBUFFERED=1 nohup .venv/bin/python py/jobs/backfill_rent.py 2026-07-01 2026-10-04 > logs/backfill.log 2>&1 &
         멈추면 같은 명령으로 이어서. API 오류는 common.fetch가 4번 재시도(1·2·4초).
"""
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from collect_rent import VIEW, save_hour          # noqa: E402
from common import alert, connect, fetch_all, job_lock, log_run   # noqa: E402

WORKERS, BATCH = 4, 8


def fetch_hour(h):
    _, rows = fetch_all("tbCycleRentData", h.strftime("%Y-%m-%d"), h.strftime("%H"))
    return h, rows


def main(a, b):
    con = connect()
    con.execute("CREATE TABLE IF NOT EXISTS rent_hours_done (q_hour TIMESTAMP PRIMARY KEY, n BIGINT, fetched_at TIMESTAMP)")
    done = {r[0] for r in con.execute("SELECT q_hour FROM rent_hours_done").fetchall()}
    con.close()
    hours = [a + dt.timedelta(hours=i) for i in range(int((b - a).total_seconds() // 3600) + 24)]
    hours = [h for h in hours if h <= b + dt.timedelta(hours=23) and h not in done]
    print(f"받을 시간대 {len(hours)}개 ({hours[0] if hours else '-'} ~ {hours[-1] if hours else '-'})", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(WORKERS) as ex:
        for i in range(0, len(hours), BATCH):
            batch = hours[i:i + BATCH]
            try:
                got = list(ex.map(fetch_hour, batch))
            except Exception as e:
                alert(f"backfill 호출 실패 {batch[0]}: {e} — 같은 명령으로 이어서 실행"); raise
            with job_lock():
                con = connect()
                try:
                    for h, rows in got:
                        save_hour(con, h, rows)
                    con.execute(VIEW)
                    log_run(con, "backfill_rent", "ok", f"{batch[0]}~{batch[-1]}")
                finally:
                    con.close()
            n = i + len(batch)
            rate = (time.time() - t0) / n
            print(f"{n}/{len(hours)} 시간대 (~{batch[-1]:%m-%d %H}시), 시간대당 {rate:.1f}초, 남은 시간 약 {rate * (len(hours) - n) / 3600:.1f}h", flush=True)
            time.sleep(3)
    print("backfill 완료", flush=True)


if __name__ == "__main__":
    main(*(dt.datetime.strptime(s, "%Y-%m-%d") for s in sys.argv[1:3]))
