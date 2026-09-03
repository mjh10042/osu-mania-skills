"""UI strings for Korean, English and Japanese.

Skillset names (Stream, Chordjack, ...) and the dan skill labels stay in English on
purpose - they are MinaCalc / mania-tracker terms that players read untranslated.
"""
from __future__ import annotations

import locale

from . import settings

FALLBACK = "en"

# Order here is the order shown in the language picker.
LANGUAGES: list[tuple[str, str]] = [
    ("ko", "한국어"),
    ("en", "English"),
    ("ja", "日本語"),
]

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "mania-skills - osu! + mamesosu skillset merger",
        "lang.label": "Language",
        "field.osu_id": "osu! name or id",
        "field.mame_id": "mamesosu (optional)",
        "btn.refresh": "Refresh",
        "btn.working": "Working...",
        "btn.find_maps": "Find maps",
        "btn.searching": "Searching...",
        "btn.pick_osu": "Choose my osu! folder",
        "dlg.pick_osu": "Select the folder containing osu!.exe",
        "msg.not_osu_folder": "That folder holds no osu!.exe or scores.db. Pick the folder"
                              " osu! itself is installed in.",
        "msg.osu_folder_set": "Saved. Press Refresh to read your local scores.",
        "btn.pick_players": "Whose scores?",
        "btn.save": "Save",
        "btn.all": "all",
        "btn.none": "none",
        "dlg.pick_players": "Choose whose scores count",
        "dlg.pick_players_hint": "osu! records whoever was logged in, so a shared PC mixes"
                                 " players. Tick every name that is yours - old names"
                                 " count too, a rename leaves clears under both.",
        "dlg.players_offline": "(offline / no name)",
        "msg.players_none": "Tick at least one name.",
        "msg.players_set": "Saved. Press Refresh to apply.",
        "status.no_cache": "no cached data - press Refresh",
        "status.cache_bad": "cache unreadable - press Refresh",
        "status.loaded": "loaded {count} cached plays",
        "status.collecting": "reading scores - both servers and this PC at once...",
        "status.rating": "rating maps {done}/{total}",
        "status.rated": "{count} plays rated",
        "status.dans": "looking up chart dans {done}/{total} - the numbers keep sharpening",
        "status.failed": "failed",
        "head.dan": "DAN",
        "head.skills": "SKILL RATINGS",
        "head.plays": "CONTRIBUTING PLAYS   (osu! {official} / mame {mame} /"
                      " combined {combined})",
        "note.dan": "Estimated from your clears with mania-tracker's model, reproduced"
                    " locally so mamesosu clears count too. Chart bucketing is"
                    " approximated from MSD, so treat this as \u00b10.2 dan.",
        "note.ranked_only": "No local osu! score database found, so only pp top plays"
                            " count - and pp only exists on ranked maps, where dan"
                            " courses do not live.",
        "note.local_on": "{count} clears read from this PC's osu! score database, which is"
                         " what lets graveyard and loved dan courses count at all.",
        "note.lazer": "Only osu!lazer was found. Lazer keeps scores in a realm database"
                      " this cannot read, so only pp top plays count. Install stable, or"
                      " point this at a stable folder below.",
        "note.thin": "Thin sample: {buckets}. These buckets average too few clears to be"
                     " trusted.",
        "note.guests": "{count} player names in this PC's score database, and all of them"
                       " are being counted. A guest's few hard clears sit right where"
                       " this estimate reads - pick your own names below.",
        "tab.plays": "Plays",
        "tab.recommend": "Recommend",
        "col.overall": "Overall",
        "col.skillset": "Skillset",
        "col.combined": "combined",
        "col.delta": "vs osu!",
        "col.src": "src",
        "col.ssr": "SSR",
        "col.acc": "acc",
        "col.wife": "wife3",
        "col.acc_od": "@OD{od}",
        "col.wife_j": "wife3 J{judge}",
        "field.system": "system",
        "field.level": "judgement",
        "sys.osu": "osu!mania",
        "sys.wife": "Etterna wife3",
        "col.spread": "±ms",
        "col.mods": "mods",
        "col.map": "map",
        "col.dan": "dan",
        "col.msd": "MSD",
        "col.focus": "focus",
        "col.bpm": "bpm",
        "col.pred": "est.",
        "col.band": "band",
        "band.push": "push",
        "band.work": "work",
        "band.solid": "solidify",
        "col.tag": "tag",
        "col.status": "status",
        "status.ranked": "ranked",
        "status.loved": "loved",
        "status.graveyard": "graveyard",
        "hint.refresh_first": "refresh first",
        "hint.target": "{skill}{auto} around dan {dan}",
        "hint.weakest": " (weakest)",
        "msg.refresh_first": "Refresh your scores first.",
        "msg.need_osu_id": "Enter your osu! username or user id.",
        "msg.no_plays": "No scores found anywhere. Check the names above, or point this"
                        " at your osu! folder so local clears can be read.",
        "err.source": "{source}: {reason} - skipped, everything else still counted.",
        "err.not_found": "no player by that name or id",
        "err.offline": "could not be reached",
        "err.server": "the server returned an error",
    },
    "ko": {
        "app.title": "mania-skills - osu! + mamesosu 스킬셋 통합",
        "lang.label": "언어",
        "field.osu_id": "osu! 이름 또는 id",
        "field.mame_id": "mamesosu (선택)",
        "btn.refresh": "새로고침",
        "btn.working": "작업 중...",
        "btn.find_maps": "맵 찾기",
        "btn.searching": "검색 중...",
        "btn.pick_osu": "내 osu! 폴더 지정",
        "dlg.pick_osu": "osu!.exe 가 있는 폴더를 선택하세요",
        "msg.not_osu_folder": "그 폴더에 osu!.exe 도 scores.db 도 없습니다."
                              " osu! 가 설치된 폴더를 골라주세요.",
        "msg.osu_folder_set": "저장했습니다. 새로고침을 누르면 로컬 기록을 읽습니다.",
        "btn.pick_players": "누구 기록인가요?",
        "btn.save": "저장",
        "btn.all": "전체 선택",
        "btn.none": "전체 해제",
        "dlg.pick_players": "반영할 계정 선택",
        "dlg.pick_players_hint": "osu! 는 그때 로그인한 이름으로 기록을 남기므로 PC 를 같이"
                                 " 쓰면 기록이 섞입니다. 본인 이름을 모두 체크하세요."
                                 " 개명 전 이름에도 클리어가 남아 있습니다.",
        "dlg.players_offline": "(오프라인 / 이름 없음)",
        "msg.players_none": "하나 이상 체크해 주세요.",
        "msg.players_set": "저장했습니다. 새로고침을 누르면 반영됩니다.",
        "status.no_cache": "저장된 데이터 없음 - 새로고침을 누르세요",
        "status.cache_bad": "캐시를 읽을 수 없음 - 새로고침을 누르세요",
        "status.loaded": "저장된 플레이 {count}개 불러옴",
        "status.collecting": "기록 수집 중 - 두 서버와 이 PC 를 동시에...",
        "status.rating": "맵 계산 중 {done}/{total}",
        "status.rated": "플레이 {count}개 계산 완료",
        "status.dans": "차트 단 조회 중 {done}/{total} - 아래 수치가 계속 정밀해집니다",
        "status.failed": "실패",
        "head.dan": "단위인정",
        "head.skills": "스킬 레이팅",
        "head.plays": "기여 플레이   (osu! {official} / mame {mame} / 합계 {combined})",
        "note.dan": "클리어 기록에서 mania-tracker 모델을 재현해 추정했습니다 (mamesosu"
                    " 클리어도 반영). 패턴 분류는 MSD로 근사하므로 \u00b10.2단 오차로"
                    " 보세요.",
        "note.ranked_only": "로컬 osu! 기록 db를 못 찾아 pp 상위 기록만 반영됩니다."
                            " pp는 ranked 맵에만 붙고 단위인정 코스는 거기 없습니다.",
        "note.local_on": "이 PC의 osu! 기록 db에서 클리어 {count}개를 읽었습니다."
                         " 무덤/럽드 단위인정 코스가 반영되는 건 이 경로 덕분입니다.",
        "note.lazer": "osu!lazer만 찾았습니다. lazer는 기록을 realm db에 저장해 읽을 수 없어"
                      " pp 상위 기록만 반영됩니다. 스테이블을 쓰거나 아래에서 스테이블"
                      " 폴더를 지정하세요.",
        "note.thin": "표본 부족: {buckets}. 이 항목은 클리어 수가 적어 신뢰도가 낮습니다.",
        "note.guests": "이 PC 기록 db 에 계정 이름이 {count}개 있고 전부 반영되고 있습니다."
                       " 손님이 남긴 소수의 고난도 클리어가 바로 이 추정치가 읽는 구간에"
                       " 놓입니다. 아래에서 본인 계정을 지정하세요.",
        "tab.plays": "플레이",
        "tab.recommend": "추천",
        "col.overall": "종합",
        "col.skillset": "스킬셋",
        "col.combined": "합계",
        "col.delta": "osu! 대비",
        "col.src": "출처",
        "col.ssr": "SSR",
        "col.acc": "정확도",
        "col.wife": "wife3",
        "col.acc_od": "@OD{od}",
        "col.wife_j": "wife3 J{judge}",
        "field.system": "체계",
        "field.level": "판정",
        "sys.osu": "osu!mania",
        "sys.wife": "Etterna wife3",
        "col.spread": "±ms",
        "col.mods": "모드",
        "col.map": "맵",
        "col.dan": "단",
        "col.msd": "MSD",
        "col.focus": "집중도",
        "col.bpm": "bpm",
        "col.pred": "예상",
        "col.band": "구간",
        "band.push": "고점 다지기",
        "band.work": "주력",
        "band.solid": "저점 다지기",
        "col.tag": "태그",
        "col.status": "상태",
        "status.ranked": "랭크",
        "status.loved": "럽드",
        "status.graveyard": "무덤",
        "hint.refresh_first": "먼저 새로고침하세요",
        "hint.target": "{skill}{auto} · 단 {dan} 부근",
        "hint.weakest": " (최약점)",
        "msg.refresh_first": "먼저 기록을 새로고침하세요.",
        "msg.need_osu_id": "osu! 유저네임 또는 유저 id 를 입력하세요.",
        "msg.no_plays": "기록을 하나도 찾지 못했습니다. 위의 이름을 확인하거나,"
                        " osu! 폴더를 지정해 로컬 클리어를 읽게 하세요.",
        "err.source": "{source}: {reason} - 이 소스만 빼고 계산했습니다.",
        "err.not_found": "그 이름이나 id 의 플레이어가 없습니다",
        "err.offline": "접속할 수 없습니다",
        "err.server": "서버가 오류를 반환했습니다",
    },
    "ja": {
        "app.title": "mania-skills - osu! + mamesosu スキルセット統合",
        "lang.label": "言語",
        "field.osu_id": "osu! 名前 または ID",
        "field.mame_id": "mamesosu (任意)",
        "btn.refresh": "更新",
        "btn.working": "処理中...",
        "btn.find_maps": "譜面を探す",
        "btn.searching": "検索中...",
        "btn.pick_osu": "osu! フォルダを指定",
        "dlg.pick_osu": "osu!.exe があるフォルダを選んでください",
        "msg.not_osu_folder": "そのフォルダには osu!.exe も scores.db もありません。"
                              "osu! をインストールしたフォルダを選んでください。",
        "msg.osu_folder_set": "保存しました。更新を押すとローカル記録を読み込みます。",
        "btn.pick_players": "誰の記録？",
        "btn.save": "保存",
        "btn.all": "全選択",
        "btn.none": "全解除",
        "dlg.pick_players": "反映するアカウントを選択",
        "dlg.pick_players_hint": "osu! はその時ログインしていた名前で記録を残すため、PC を"
                                 "共有すると記録が混ざります。自分の名前をすべてチェックして"
                                 "ください。改名前の名前にもクリアが残っています。",
        "dlg.players_offline": "(オフライン / 名前なし)",
        "msg.players_none": "1つ以上チェックしてください。",
        "msg.players_set": "保存しました。更新を押すと反映されます。",
        "status.no_cache": "キャッシュなし - 更新を押してください",
        "status.cache_bad": "キャッシュを読めません - 更新を押してください",
        "status.loaded": "キャッシュしたプレイ {count} 件を読み込み",
        "status.collecting": "スコア収集中 - 両サーバーとこのPCを同時に...",
        "status.rating": "譜面計算中 {done}/{total}",
        "status.rated": "{count} 件のプレイを計算しました",
        "status.dans": "譜面段位を取得中 {done}/{total} - 下の数値は精度が上がり続けます",
        "status.failed": "失敗",
        "head.dan": "段位認定",
        "head.skills": "スキルレーティング",
        "head.plays": "貢献プレイ   (osu! {official} / mame {mame} / 合計 {combined})",
        "note.dan": "クリア記録から mania-tracker のモデルを再現して推定しています"
                    " (mamesosu のクリアも反映)。譜面の分類は MSD による近似なので、"
                    "\u00b10.2 段の誤差と考えてください。",
        "note.ranked_only": "ローカルの osu! スコア db が見つからないため pp 上位記録のみ"
                            "反映されます。pp は ranked 譜面にしか付かず、段位認定は"
                            "そこにありません。",
        "note.local_on": "この PC の osu! スコア db からクリア {count} 件を読み込みました。"
                         "graveyard/loved の段位認定コースが数えられるのはこの経路です。",
        "note.lazer": "osu!lazer しか見つかりません。lazer はスコアを realm db に保存するため"
                      "読めず、pp 上位記録のみ反映されます。stable を使うか、下から stable "
                      "フォルダを指定してください。",
        "note.thin": "サンプル不足: {buckets}。クリア数が少なく信頼度が低い項目です。",
        "note.guests": "この PC のスコア db にアカウント名が {count} 個あり、すべて反映されて"
                       "います。ゲストが残した少数の高難度クリアは、まさにこの推定値が読む"
                       "位置に来ます。下から自分のアカウントを指定してください。",
        "tab.plays": "プレイ",
        "tab.recommend": "おすすめ",
        "col.overall": "総合",
        "col.skillset": "スキルセット",
        "col.combined": "合計",
        "col.delta": "osu! 比",
        "col.src": "取得元",
        "col.ssr": "SSR",
        "col.acc": "精度",
        "col.wife": "wife3",
        "col.acc_od": "@OD{od}",
        "col.wife_j": "wife3 J{judge}",
        "field.system": "体系",
        "field.level": "判定",
        "sys.osu": "osu!mania",
        "sys.wife": "Etterna wife3",
        "col.spread": "±ms",
        "col.mods": "Mod",
        "col.map": "譜面",
        "col.dan": "段",
        "col.msd": "MSD",
        "col.focus": "集中度",
        "col.bpm": "bpm",
        "col.pred": "予想",
        "col.band": "区間",
        "band.push": "高点固め",
        "band.work": "主力",
        "band.solid": "低点固め",
        "col.tag": "タグ",
        "col.status": "状態",
        "status.ranked": "Ranked",
        "status.loved": "Loved",
        "status.graveyard": "墓地",
        "hint.refresh_first": "先に更新してください",
        "hint.target": "{skill}{auto} · 段 {dan} 付近",
        "hint.weakest": " (最弱)",
        "msg.refresh_first": "先にスコアを更新してください。",
        "msg.need_osu_id": "osu! のユーザー名またはユーザーIDを入力してください。",
        "msg.no_plays": "スコアが見つかりませんでした。上の名前を確認するか、"
                        "osu! フォルダを指定してローカルのクリアを読み込ませてください。",
        "err.source": "{source}: {reason} - このソースを除いて計算しました。",
        "err.not_found": "その名前またはIDのプレイヤーが見つかりません",
        "err.offline": "接続できません",
        "err.server": "サーバーがエラーを返しました",
    },
}


def _system_language() -> str:
    try:
        code = (locale.getdefaultlocale()[0] or "").lower()
    except ValueError:
        return FALLBACK
    for lang, _ in LANGUAGES:
        if code.startswith(lang):
            return lang
    return FALLBACK


def _load_language() -> str:
    saved = settings.load().get("language")
    return saved if saved in _STRINGS else _system_language()


_current = _load_language()


def language() -> str:
    return _current


def language_name(code: str) -> str:
    return dict(LANGUAGES).get(code, code)


def set_language(code: str) -> None:
    """Switch language and remember it for the next launch."""
    global _current
    if code not in _STRINGS:
        return
    _current = code
    settings.update(language=code)


def t(key: str, **kw) -> str:
    s = _STRINGS[_current].get(key) or _STRINGS[FALLBACK].get(key) or key
    return s.format(**kw) if kw else s
