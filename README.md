# Discord Bot

다양한 유틸리티 및 음악 기능을 제공하는 Discord 봇입니다. 이전에 공개한 코드들을 개선, 수정하여 모듈화 시켰습니다.

음악 재생, 취침 모드 제어, 경기 팀 매칭, 추첨 기능 등을 통해 서버 관리와 즐거운 상호작용을 돕습니다.

(GPT로 작성된 README 파일입니다.)

---

## 📦 주요 기능

### steam.py - Compare Country Price
- Detects Steam store page links in messages  
- Deletes the original message  
- Sends an embed with game info and price comparison  
- Compares prices between two regions defined in `config.json`  
- Includes discount info and currency conversion  
- Requires valid region settings in `config.json`

### [🎵 music.py - 음악 재생 봇](https://github.com/mangddung/music-bot)
- 유튜브 링크 또는 검색어 기반으로 음악을 재생합니다.
- 음악 대기열 관리 및 자동 재생을 지원합니다.
- 전용 텍스트 채널 생성 및 패널 UI 제공.
- 스포티파이 연동 재생 (디스코드 스포티파이 활동을 기준으로 재생합니다. 스포티파이 연동이 필요합니다.)
- `FFmpeg`와 `yt-dlp` 기반으로 고음질 스트리밍.

#### 슬래쉬 명령어 명령어
| 명령어       | 설명                                      |
|--------------|-------------------------------------------|
| `전용채널`  | 음악봇 명령을 받을 전용 채널을 생성합니다. |
| `패널재생성`| 전용 패널 메시지를 재생성합니다.          |
| `스포티파이`| 스포티파이 연동 재생을 시작합니다. |

---

### [💤 sleep_mode.py - 취침 모드](https://github.com/mangddung/discord-bot)
- 유저가 설정한 시간 동안 보이스 채널 접속을 제한합니다.
- 평일/휴일 설정 지원 및 자동 퇴장/알림 기능 제공.
- 데이터는 SQLite 및 SQLAlchemy ORM으로 저장됩니다.

> 슬래시 명령어 기반 설정 사용 (`/취침모드 설정` 등)

---

### [🏅 match_maker.py - 경기 팀 생성 도우미](https://github.com/mangddung/discord-match-maker-bot)
- 경기 참가자 등록 및 팀 자동 분배 기능을 제공합니다.
- 참가자들을 보이스 채널별로 분산 또는 통합 이동 가능.
- 동일 명령자 기반으로 복수 경기 지원.

> 슬래시 명령어 기반 기능

---

### [🔍 find_common_match.py - 롤 공통 매치 찾기](https://github.com/mangddung/find_riot_common_match)
- Riot API 기반으로 두 플레이어의 최근 경기 중 공통 매치를 찾습니다.
- 최대 300경기까지 검색 가능하며, 결과는 링크로 출력됩니다.

> 슬래시 명령어 기반 기능

---

### 🎲 funny.py - 랜덤 추첨 유틸
- 숫자 범위 내 난수 추첨 기능
- 문자열 리스트 중 하나 랜덤 선택
- 보이스 채널 참가자 중 랜덤 유저 선택

> 슬래시 명령어 기반 기능

---

### 🎮 mc_server.py - Minecraft Server Monitor
- Real-time monitoring of Minecraft server status
- Automatic updates every 5 minutes with live player count and server info
- Shared server data across multiple guilds to prevent duplicate queries
- Domain-to-IP resolution for server address validation
- Dedicated read-only channels with embedded status panels
- Abuse prevention with rate limiting and guild-based registration caps

#### Slash Commands
| Command       | Description                                      |
|--------------|--------------------------------------------------|
| `/mcs_add`   | Creates a monitoring channel with server details |
| `/mcs_remove`| Removes a server monitoring channel              |
| `/mcs_list`  | Shows all registered servers in the guild        |
| `/mcs_update`| Manually updates server status panels            |

#### Features
- **Efficient Querying**: Servers are queried only once every 30 seconds, even if monitored by multiple guilds
- **Auto-cleanup**: Automatically removes database entries when channels are deleted
- **Security**: Rate limiting (3 registrations per minute) and registration caps (10 servers per guild)
- **Smart Caching**: Stores both domain names and resolved IPs for reliability

---

## 🛠 설치 방법

### 방법 1: 로컬 환경
1. 이 저장소를 클론합니다.
2. Python 환경에서 다음 명령어를 실행하여 패키지를 설치하세요:
```
pip install -r requirements.txt
```
3. music 기능을 사용할려면 FFmpeg를 운영체제에 맞게 설치해주세요.
4. .env에 API키 및 FFmpeg 경로를, config.json에는 봇 환경을 설정합니다.
5. main.py를 실행하세요.

### 방법 2: Docker
1. 이 저장소를 클론합니다.
2. Docker를 설치해주세요.
3. .env에 API키를, config.json에는 봇 환경을 설정합니다.
4. 다음 명령어로 Docker 컨테이너를 빌드하고 실행합니다.
```
 docker-compose up --build -d
```

# 업데이트 기록
- 2025-07-20
 1. 스포티파이 연동 재생 기능 추가 (디스코드내 스포티파이 활동 기준)
 2. 슬래시 커맨드 도입
 3. 패널 디자인 개선
 4. DB 구조 변경
