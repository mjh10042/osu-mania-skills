# mania-skills

osu!mania 4K 스킬셋 분석기. osu! 공식 서버, mamesosu, 그리고 이 PC의 `scores.db` 세 곳에
흩어진 기록을 하나로 합쳐서 스킬셋별 레이팅과 단위인정(dan)을 계산하고, 약한 스킬셋을
겨냥한 맵을 추천합니다.

두 서버 어느 쪽도 상대방 플레이를 모르기 때문에 "합친 진짜 실력"이 나오지 않는 것을
해결하려고 만들었습니다.

## 무엇을 하는가

**세 소스를 동시에 수집합니다.** 온라인 두 곳은 top play만 주지만, 단위인정은 무덤·러브드
채보 클리어로 갈립니다. 그래서 로컬 `scores.db` 로 대부분의 기록을 가져올 수 있습니다

**MSD를 로컬에서 계산합니다.** 번들된 MinaCalc CLI(`vendor/msd.exe`)로 채보마다 8개
스킬셋 값을 뽑고, Etterna의 AggregateSSRs 방식으로 플레이어 레이팅을 만듭니다
(mania-tracker 공표값을 rmse 0.24로 재현).

**단(dan)을 로컬에서 재현합니다.** mania-tracker의 `dan-evidence`를 역설계해서 구현했기
때문에, 그쪽이 볼 수 없는 mamesosu·로컬 클리어도 같은 기준으로 채점됩니다.

**정확도를 두 체계로 보여줍니다.** osu!mania는 판정을 여섯 칸으로 나눠 가중치를 주고,
Etterna의 wife3는 ms 단위 오차를 연속 곡선으로 채점하며 미스를 −2.75노트로 칩니다. 같은
93%가 아닙니다. 판정 개수에서 타이밍 편차를 역추정해 임의의 OD·판정 난이도로 환산합니다.

**맵을 추천합니다.** 그냥 맵만 추천합니다

## 실행

```
python app.py
```

Python 3.12+ 와 `requests`, `pillow`(아이콘 생성용)가 필요합니다. Windows 전용입니다 —
`scores.db` 파싱과 번들 바이너리가 stable 클라이언트를 전제로 합니다.

배포용 단일 실행 파일:

```
python scripts/release.py
```

빌드하고, 빌드한 기계의 개인 데이터가 산출물에 섞이지 않았는지 검사하고, 빈 캐시로 한 번
실행해 첫 실행 화면이 실제로 비어 있는지 확인한 뒤 zip으로 묶습니다.

## 구조

| | |
|---|---|
| `core/sources.py` | osu! 공식(mania-tracker 경유)과 mamesosu 조회 |
| `core/local_scores.py` | `scores.db` 파싱 |
| `core/beatmap.py` | `.osu` 파일 탐색·파싱, Songs 폴더 색인 |
| `core/msd.py` | 번들 MinaCalc 래퍼 |
| `core/aggregate.py` | MSD → 스킬셋 레이팅 |
| `core/dan.py` | 단 변환과 플레이어 단 추정 |
| `core/wife.py` | Etterna wife3 정확도 추정·환산 |
| `core/predict.py` | 기록에서 예상 정확도 회귀 |
| `core/recommend.py` | 맵 추천 |
| `scripts/` | 빌드, 아이콘 생성, 모델 재적합, 검증 |

각 모듈 상단 주석에 "왜 이렇게 되어 있는지"를 적어뒀습니다. 상당수가 측정해서 알아낸
것이라 숫자가 함께 있습니다.

## 개인정보

앱이 학습한 것(osu!/mamesosu id, 플레이 기록, 비트맵 폴더 색인)은 전부 소스 트리 밖
`%LOCALAPPDATA%\mania-skills\cache`에 저장됩니다. 배포물이 정의상 파일 하나가 되도록
하기 위해서고, 그래서 exe를 남에게 건네도 캐시가 따라가지 않습니다.

`scripts/release.py`가 빌드마다 이를 검사합니다 — 실행 파일과 그 안에 압축된 모든 엔트리를
풀어서 빌드 기계의 id·경로·`scores.db`의 플레이어 이름을 문자열로 찾고, 하나라도 나오면
빌드를 중단합니다.

## 알려진 것

- 백신이 오탐할 수 있습니다. 서명 없는 PyInstaller 단일 파일은 실행할 때마다 자신을
  `%TEMP%`에 풀고 번들 바이너리를 창 없이 실행하는데, 이는 드로퍼의 행동 프로필과
  통계적으로 닮았습니다. Microsoft에는 오탐 신고가 접수되어 정상 판정을 받았습니다.
- `vendor/msd.exe`는 MinaCalc CLI 빌드로, 이 저장소의 코드가 아닙니다.
- osu!lazer는 지원하지 않습니다. 스코어를 realm 데이터베이스에 두는데 읽을 수 없습니다.
