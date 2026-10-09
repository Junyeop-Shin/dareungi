# 배포 B — 서버 1대 (우분투 22.04/24.04 기준)

수집기(cron 대신 systemd 타이머) + 운영 DB + 스냅샷 + Streamlit + Caddy(HTTPS)를 한 VM에 올린다. 결정 로그 2026-10-08.

## 서버 사양·준비
- 2 vCPU, 4GB RAM, 디스크 30GB 이상(운영 DB는 최근 120일 기준 수 GB). 시간대 `Asia/Seoul`.
- 열어야 할 포트: 80, 443(Caddy). 8501은 밖에 열지 않는다(Caddy가 내부로 넘김).
- 서울 열린데이터 API는 **8088 포트 평문 HTTP**다. 바깥 연결(egress)이 막혀 있지 않은지 확인.
- 도메인 하나(없으면 DuckDNS 등 무료 서브도메인)를 서버 IP로 연결. Caddy가 인증서를 자동 발급한다.

## 설치 순서 (2026-10-10에 AWS Lightsail 서울, Ubuntu 24.04 · 4GB · x86에서 이 순서로 올렸다)

서버를 만들 때: 고정 IP를 붙이고, 방화벽에 HTTPS(443)를 추가한다(22·80은 기본). 도메인이 없으면 `<고정IP>.sslip.io`를 주소로 쓴다(가입 없이 인증서까지 나온다).

**1. 서버에서: 코드 받고 설치** (비공개 저장소라 서버에서 만든 공개 키를 깃허브 Deploy keys에 읽기 전용으로 등록한다)
```bash
sudo timedatectl set-timezone Asia/Seoul
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub     # 이 한 줄을 깃허브에 등록
git clone git@github.com:Junyeop-Shin/dareungi.git ~/dareungi
cd ~/dareungi && bash deploy/setup.sh       # 파이썬 venv·패키지(약 10분, torch가 크다)·Caddy·systemd. 타이머는 등록만 하고 켜지 않는다
nano .env                                   # SEOUL_API_KEY=..., DATA_GO_KR_KEY=..., ALERT_WEBHOOK=...
```

**2. 맥북에서: 깃에 없는 자료 옮기기.** 운영 DB는 **그냥 복사하면 안 된다.** 예약 작업이 쓰는 중에 복사하면 깨진 파일이 간다(실제로 겪었다: 서버에서 `Failed to load metadata pointer`). 잠근 채로 사본을 만들어 그 사본을 보낸다.
```bash
cd ~/dareungi
.venv/bin/python -c "
import sys, shutil; sys.path.append('py'); import common
with common.job_lock():
    con = common.connect(); con.execute('CHECKPOINT'); con.close()
    shutil.copyfile('data/db/ops.duckdb', 'data/db/ops_copy.duckdb')"
md5 -q data/db/ops_copy.duckdb                                   # 서버에서 md5sum으로 같은 값인지 본다
scp data/db/ops_copy.duckdb <서버>:~/dareungi/data/db/ops.duckdb
scp data/interim/station_master.csv data/interim/station_cluster.csv <서버>:~/dareungi/data/interim/
scp -r serving <서버>:~/dareungi/
ssh <서버> "mkdir -p ~/dareungi/data/interim/weather_forecast"
scp data/interim/weather_forecast/kma_by_origin.parquet <서버>:~/dareungi/data/interim/weather_forecast/
```
분석 DB(22GB)는 옮기지 않는다. 모형(`output/models/`)과 마스터·공휴일 표는 깃에 있다.

**3. 서버에서: 주소** (지금 주소: `https://ttareungi-care.duckdns.org`, DuckDNS 무료 서브도메인을 고정 IP에 연결. 누구나 볼 수 있게 열어 둠)
```bash
sudo cp ~/dareungi/deploy/Caddyfile /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile              # 주소가 다르면 내 것으로. 잠그려면 basicauth 블록의 #을 지우고 'caddy hash-password'의 해시를 넣는다
```

**4. 서버에서: 켜고 확인**
```bash
sudo systemctl restart caddy dareungi-app-admin
.venv/bin/python py/common.py alert-test    # 슬랙에 시험 알림
.venv/bin/python py/jobs/hourly.py | tail -3
for t in collect_rent detect_sham hourly collect_wx_fcst collect_wx_obs daily weekly collect_station; do sudo systemctl start dareungi-$t.timer; done
systemctl list-timers | grep -c dareungi    # 8
```
멈춘 타이머는 `systemctl start 'dareungi-*.timer'`처럼 `*`로는 켜지지 않는다(이름을 하나씩 준다).

**5. 맥북 예약 끄기**: 서버가 한두 시간 정상으로 도는 것을 본 뒤 맥북에서 `crontab -r`. 안 끄면 두 곳에서 수집하고 경보도 두 번 온다.

## 서비스
- `dareungi-app-admin.service`(8501): Streamlit 관리자 앱 상시 실행(로컬만). Caddy가 HTTPS 주소로 연결한다(비밀번호 없이 공개, 결정 로그 2026-10-10). 시민 조회 앱은 2026-10-10에 없앴다(자전거 조회는 관리자 앱의 한 화면).
- `dareungi-<job>.timer/.service`: cron 대신 systemd 타이머(맥북 cron과 같은 주기). `systemctl list-timers`로 확인.
- Caddy: 설정은 `/etc/caddy/Caddyfile`(위 3번). 설치 스크립트는 이 파일을 건드리지 않는다.

## 설치 뒤 확인
- 경보: `.venv/bin/python py/common.py alert-test` → 슬랙(또는 디스코드) 채널에 시험 알림이 오는지
- 시험: `.venv/bin/python tests/test_ops.py`, `tests/test_analysis.py`, `tests/smoke_app.py`(스냅샷이 발행된 뒤)
- 화면: 서버에서 앱을 띄운 뒤 `tests/screens.py`(크롬과 playwright가 있어야 한다. 없으면 브라우저로 네 화면을 직접 확인)
- ARM 서버(예: Oracle Cloud 무료 등급)에서는 이 설치 스크립트를 돌려 보지 않았다(x86 우분투 24.04 · 파이썬 3.12에서는 `requirements.txt` 그대로 설치됐다)

## 운영 전 점검표(런북 07·08)
- [ ] 같은 시간대 두 번 수집해도 결과 같음(멱등)
- [ ] 서버를 1시간 멈췄다 켜도 빠진 시간대가 채워짐(따라잡기)
- [ ] 잘못된 키로 실행 → 경보
- [ ] 앱이 `serving/*.parquet`만 읽음(DB 잠금 없음)
- [ ] `git log -p | grep <키 앞 6자>` 결과 없음, 로그·스냅샷에 키 없음
- [ ] HTTPS 주소로 접속, 스냅샷 48시간 넘으면 경고 뜸
- [ ] 2주 연속 무중단 수집 로그
