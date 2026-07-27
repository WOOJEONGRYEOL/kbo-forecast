# statiz.co.kr 크롤러 (crawl4ai)

KBO 통계 사이트 [statiz.co.kr](https://www.statiz.co.kr)에서 **선수 기록(타자/투수)·팀 순위**를
헤드리스 브라우저(crawl4ai + Playwright)로 긁어 **CSV**로 저장한다.

## 왜 로그인이 필요한가
직접 확인한 결과, statiz는 상세 통계 대부분을 로그인 뒤에 숨겨두고 Cloudflare 봇 차단도 걸어놨다.

| 페이지 | 로그인 없이 |
|---|---|
| 메인 `/` | ✅ WAR 선수 랭킹 + 팀 순위 요약표 |
| 선수 종합 `?m=total` | ❌ 로그인 리다이렉트 |
| 팀 `?m=team` | ❌ 로그인 리다이렉트 |

→ "최대한 많이"는 **본인 statiz 계정**으로 로그인해야 가능하다.

## 설치 (이미 완료된 상태)
```bash
# 저장소 루트에서
python3.11 -m venv .venv_statiz
source .venv_statiz/bin/activate
pip install crawl4ai pandas lxml beautifulsoup4
crawl4ai-setup      # Playwright chromium 설치
```

## 사용법

### 0) 자격증명은 환경변수로 (코드/기록에 안 남음)
```bash
export STATIZ_ID='본인이메일'
export STATIZ_PW='본인비밀번호'
```

### 1) (선택) discover — 로그인 검증 + 구조 덤프
```bash
python statiz_crawl.py discover
```
`logged_in: true` 면 로그인 성공. 파라미터 공식은 이미 확정돼 있어 보통은 건너뛰어도 된다.

### 2) crawl — 표를 CSV로 저장
```bash
# 특정 연도들 (기본: 규정타석/규정이닝 이상만)
python statiz_crawl.py crawl --years 2024 2023 2022 --delay 3

# 연도 범위 (최신부터)
python statiz_crawl.py crawl --from-year 1982 --to-year 2026 --delay 3

# 규정 필터 없이 전체 선수
python statiz_crawl.py crawl --years 2024 --all-players
```
연도마다 아래 4개 CSV를 만든다 (`output/csv/`, 엑셀 한글 호환 `utf-8-sig`):

| 파일 | 내용 |
|---|---|
| `batter_<연도>.csv` | 그 시즌 **규정타석 이상** 타자 (33열 세이버매트릭스) |
| `pitcher_<연도>.csv` | 그 시즌 **규정이닝 이상** 투수 (36열) |
| `team_batting_<연도>.csv` | 10개 구단 타격 합계 (필터 없음) |
| `team_pitching_<연도>.csv` | 10개 구단 투구 합계 (필터 없음) |

**규정 기준(KBO):** 규정타석 = 팀경기수 × 3.1, 규정이닝 = 팀경기수 × 1.0.
팀경기수는 `team_batting` 표의 `G`에서 자동으로 읽어 **연도마다 정확히** 적용된다
(예: 2024년 144경기 → 규정타석 446, 규정이닝 144). `--all-players` 로 필터 해제.

`output/manifest.json` 에 무엇을 몇 행 받았는지 기록.

### 통계 URL 공식 (역설계 완료)
statiz 통계 폼(`frm_searchSeason`, GET `/stats/`)의 전체 파라미터 중 핵심:
- `m=total`(선수) / `m=team`(팀), `m2=batting|pitching|fielding`
- `sy`/`ey` = 시작/종료 연도 (단일 시즌은 `sy=ey`)
- `reg` = 규정/누적 타석 하한. **`""`(빈값)=전체 선수**, `C3000`=통산 3000타석 등
- `pr` = 한 페이지 행 수. `1000`이면 한 시즌 전체가 1페이지 (⚠️ `2000` 이상은 서버가 10으로 리셋)
- `so=WAR&ob=DESC` = WAR 내림차순 정렬

나머지 40여 개 파라미터는 빈값 기본. `stat_url(...)` 로 조립하고 `build_targets()` 로 연도별 생성.

## ⚠️ Cloudflare 봇 차단 & 이어받기(resume)
statiz는 Cloudflare 안티봇이 있어, 한 세션에서 **약 20~40회 요청 후 HTTP 403**으로
차단하기 시작한다(near-empty 403). 그래서 한 번에 전 연도를 못 받고 최근 5시즌 정도씩
끊긴다. 차단되면 수 분~수십 분 쿨다운이 필요하다.

**대응:** 차단이 풀린 뒤 `--skip-existing` 으로 빠진 연도만 이어받는다.
```bash
# 이미 받은 연도는 건너뛰고, 넉넉한 간격/재시도로 나머지 채우기
python statiz_crawl.py crawl --from-year 1982 --to-year 2026 \
    --skip-existing --delay 8 --retries 3 --retry-wait 30
```
차단이 다시 걸리면 잠시 뒤 같은 명령을 반복하면 조금씩 채워진다.
급하지 않게, 하루에 몇 번 나눠 돌리는 걸 권장(사이트 부담·차단 최소화).

## 시즌 갱신 / 유지보수 (업데이트)
크롤러는 연도를 동적으로 처리하므로 **코드 수정 없이** 해당 연도만 다시 돌리면 된다.

### 이번 시즌(진행 중) 최신화
경기가 진행될수록 기록이 바뀌므로, 원할 때마다 그 해만 다시 받아 덮어쓴다:
```bash
./update.sh            # 올해 자동
./update.sh 2026       # 연도 지정
```
- ⚠️ 갱신은 `--skip-existing` **없이** 돌려 기존 파일을 덮어쓴다(`update.sh`가 그렇게 함).
- 규정타석/이닝 기준은 팀 경기수에서 자동 계산 → 시즌 중엔 낮게, 시즌 종료 후 다시 돌리면
  최종 기준(144경기 기준 규정타석 446)으로 확정된다. **시즌이 끝나면 한 번 더 돌려** 마무리.

### 내년 새 시즌(2027 등) 추가
새 시즌이 시작되면 그냥 그 연도로 돌리면 파일이 새로 생긴다. 코드 변경 불필요:
```bash
./update.sh 2027
```

### 자동화 (launchd — 이미 등록됨)
**매일 새벽 5시** 자동 갱신하도록 macOS launchd 에 등록되어 있다.
오프시즌(12~2월)은 `auto_update.sh` 가 자동으로 건너뛴다. Mac이 잠들어 있으면
다음에 깨어날 때 실행된다.

- 등록 파일: `~/Library/LaunchAgents/com.statiz.autoupdate.plist`
- 래퍼: `auto_update.sh` (오프시즌 판단 → `update.sh` 호출)
- 실행 로그: `output/cron.log`, `output/launchd.out.log` / `.err.log`

관리 명령:
```bash
UID=$(id -u)
launchctl list | grep statiz                                   # 등록 상태 확인
launchctl kickstart -k gui/$UID/com.statiz.autoupdate          # 지금 즉시 1회 실행(테스트)
launchctl bootout gui/$UID/com.statiz.autoupdate               # 자동화 해제
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.statiz.autoupdate.plist  # 다시 등록
```
시간을 바꾸려면 plist 의 `Hour`/`Minute` 를 고치고 bootout→bootstrap 다시 하면 된다.
(하루 1회 권장 — Cloudflare 차단·서버 부담 최소화.)

## 이메일 알림 + Google Sheets 자동 저장 (설정 필요)
자동 잡은 저(Claude) 없이 새벽에 돌기 때문에, 이메일·시트 연동은 **스크립트 자체 자격증명**이
필요하다. 아래 값을 `~/.statiz_env` 에 넣으면 매 갱신 때 자동으로 ① 로그 이메일 발송,
② 이번 연도 CSV 를 시트에 업로드한다. (값이 없으면 각각 조용히 스킵되고 크롤은 정상 동작)

### A. 갱신 로그 이메일 (Gmail SMTP)
1. Google 계정에 **2단계 인증**을 켠다.
2. https://myaccount.google.com/apppasswords 에서 **앱 비밀번호**(16자리) 생성.
3. `~/.statiz_env` 에 추가:
   ```bash
   export STATIZ_MAIL_APP_PW='xxxx xxxx xxxx xxxx'   # 앱 비밀번호(일반 비번 아님)
   # export STATIZ_MAIL_TO='다른주소@example.com'    # 생략 시 본인 지메일로
   ```
   → `send_log_email.py` 가 `jeongryeolwoo@gmail.com` 로 로그인해 본인에게 발송.

### B. CSV → Google Sheets (서비스 계정)
1. https://console.cloud.google.com 에서 프로젝트 생성 → **Google Sheets API** 사용 설정.
2. **서비스 계정** 생성 → 키(JSON) 다운로드 → 예: `~/.statiz_sa.json` 로 저장.
3. 저장할 **Google 시트를 새로 만들고**, 서비스계정 이메일
   (`...@....iam.gserviceaccount.com`)을 그 시트에 **편집자로 공유**.
4. `~/.statiz_env` 에 추가:
   ```bash
   export GOOGLE_SERVICE_ACCOUNT_JSON="$HOME/.statiz_sa.json"
   export STATIZ_GSHEET_ID='시트URL의 /d/<여기>/edit 부분'
   ```
   → 매 갱신 때 그 해 파일(타자/투수/팀 + `_all`)이 파일명 탭으로 업로드된다.

**전 시즌(1982~) 히스토리를 한 번에 시트로 올리려면**(탭 270개, 시간 걸림):
```bash
source ~/.statiz_env && python push_to_sheets.py   # 인자 없이 = output/csv 전체
```

## 매너 / 주의
- **본인 계정으로, 개인 분석 용도로만** 사용할 것.
- concurrency=1, `--delay` 기본 3초로 서버 부담을 줄인다. 대량 수집 시 delay를 더 늘릴 것.
- statiz 이용약관을 위반하지 않는 선에서 사용. 상업적 재배포 금지.
- 로그인 없이도 메인 페이지의 팀 순위·WAR 랭킹은 받을 수 있다:
  `python statiz_crawl.py crawl --url "https://www.statiz.co.kr/"`

## 파일
- `statiz_crawl.py` — 크롤러 본체 (discover / crawl 서브커맨드)
- `output/` — 수집 결과 (git 제외)
