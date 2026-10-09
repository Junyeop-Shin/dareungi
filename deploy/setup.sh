#!/usr/bin/env bash
# 서버 1대 배포 설치 스크립트 (우분투). 저장소 루트에서: bash deploy/setup.sh
# 하는 일: 파이썬 venv + requirements, Caddy 설치, systemd 서비스·타이머 등록. .env는 손으로 만든다(키는 깃에 없다).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)
USER_NAME=$(whoami)

sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git curl libgomp1   # libgomp1: lightgbm이 리눅스에서 쓴다
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
mkdir -p data/db data/interim serving logs

# Caddy (HTTPS 자동): 우분투 기본 저장소 판(24.04는 2.6.2). Caddy 공식 저장소(cloudsmith)는 2026-10-10에 402로 거절돼 쓰지 않는다.
# 설정 파일(/etc/caddy/Caddyfile)은 여기서 건드리지 않는다. 주소와 비밀번호를 넣는 일은 deploy/README.md 순서대로 손으로 한다
# (전에는 예시 파일을 매번 덮어써서, 다시 실행하면 넣어 둔 주소·비밀번호가 지워졌다).
sudo apt-get install -y caddy

# Streamlit 상시 실행: 관리자 앱(8501) 하나. 시민 조회 앱은 2026-10-10에 없앴다(자전거 조회는 관리자 앱의 한 화면)
for app in admin:app/admin/Home.py:8501; do
  IFS=':' read -r name path port <<<"$app"
  sudo tee /etc/systemd/system/dareungi-app-$name.service >/dev/null <<EOF
[Unit]
Description=dareungi $name app (Streamlit)
After=network.target
[Service]
User=$USER_NAME
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/streamlit run $path --server.headless true --server.port $port --server.address 127.0.0.1
Restart=always
[Install]
WantedBy=multi-user.target
EOF
done

# 수집·갱신 작업: systemd 타이머 (cron과 같은 주기)
declare -A JOBS=(
  [collect_rent]="*-*-* *:15:00|py/jobs/collect_rent.py"
  [detect_sham]="*-*-* *:20:00|py/jobs/detect_sham.py"
  [hourly]="*-*-* *:25:00|py/jobs/hourly.py"
  [collect_wx_fcst]="*-*-* 05,17:20:00|py/jobs/collect_wx.py fcst"
  [collect_wx_obs]="*-*-* 06:30:00|py/jobs/collect_wx.py obs"
  [daily]="*-*-* 07:00:00|py/jobs/daily.py"
  [weekly]="Mon *-*-* 07:30:00|py/jobs/weekly.py"
  [collect_station]="Mon *-*-* 05:00:00|py/jobs/collect_station.py"
)
for name in "${!JOBS[@]}"; do
  IFS='|' read -r cal cmd <<<"${JOBS[$name]}"
  sudo tee /etc/systemd/system/dareungi-$name.service >/dev/null <<EOF
[Unit]
Description=dareungi job $name
[Service]
Type=oneshot
User=$USER_NAME
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=$ROOT/.venv/bin/python $cmd
StandardOutput=append:$ROOT/logs/$name.log
StandardError=append:$ROOT/logs/$name.log
EOF
  sudo tee /etc/systemd/system/dareungi-$name.timer >/dev/null <<EOF
[Unit]
Description=dareungi timer $name
[Timer]
OnCalendar=$cal
Persistent=true
[Install]
WantedBy=timers.target
EOF
done
sudo systemctl daemon-reload
sudo systemctl enable --now dareungi-app-admin.service
# 타이머는 등록만 하고 켜지 않는다. .env와 운영 DB가 없는 채로 돌면 빈 DB가 생기고, DB를 옮기는 중에 돌면 파일이 깨진다.
# 이미 켜져 있는 타이머는 그대로 돈다(다시 실행해도 멈추지 않는다).
for name in "${!JOBS[@]}"; do sudo systemctl enable dareungi-$name.timer; done
echo "설치 끝. deploy/README.md 순서대로 .env와 운영 DB를 넣은 뒤 타이머를 켠다:"
echo "  for t in ${!JOBS[*]}; do sudo systemctl start dareungi-\$t.timer; done"
