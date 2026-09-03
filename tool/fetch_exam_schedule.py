#!/usr/bin/env python3
"""조주기능사 시험 일정·합격률을 공공데이터포털에서 받아 JSON으로 굳힌다.

왜 앱이 API를 직접 부르지 않는가
--------------------------------
개발계정 쿼터가 **1,000회/일**이다. 앱에서 직접 부르면 사용자 수에
비례해 호출이 늘어 500명만 넘어도 그날치가 소진되고, 그때부터는
**아무도** 일정을 못 본다. 게다가 키를 앱에 넣으면 디컴파일로 새어
나간다.

시험 일정은 1년에 몇 번 바뀌는 데이터다. 하루 한 번 여기서 받아
JSON으로 저장소에 커밋해두고 앱은 그 정적 파일만 읽는다. 그러면
호출은 하루 몇 번으로 고정되고, 키는 GitHub Secrets 밖으로 안 나간다.

실행: DATA_GO_KR_KEY=... python3 tool/fetch_exam_schedule.py
"""

# 러너의 파이썬 판이 무엇이든 돌아가야 한다. 타입 표기를 문자열로
# 미뤄두면 3.9에서도 `str | None`이 문법 오류가 되지 않는다.
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote, unquote

# 조주기능사 종목코드. 큐넷 종목 페이지 URL의 jmCd 값이다.
JM_CD = "7916"

# 국가기술자격. 일정 API의 qualgbCd.
QUAL_GB_CD = "T"

SCHEDULE_URL = "https://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList"
PASS_RATE_URL = (
    "http://openapi.q-net.or.kr/api/service/rest/InquiryQualPassRateSVC/getList"
)

OUT_PATH = os.environ.get("OUT_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "exam_schedule.json",
)

TIMEOUT = 30

# 포털이 한 페이지 50건을 넘기면 resultCode 930으로 거절한다.
PAGE_SIZE = 50

# 합격률은 전 종목이 섞여 오는데 총 건수를 미리 알 수 없다. 응답이
# 계속 가득 차 오는 경우를 대비한 안전장치 — 하루 쿼터가 1,000회다.
MAX_PAGES = 20


def service_key() -> str:
    """인증키를 쿼리에 넣을 수 있는 형태로 만든다.

    포털이 주는 키에는 `/`와 `=`가 들어 있고, 화면에는 인코딩된 것과
    안 된 것이 나란히 표시된다. 어느 쪽이 환경변수로 들어와도 되도록
    한 번 풀었다가 다시 인코딩한다 — 이미 인코딩된 키를 또 인코딩해서
    `%2F`가 `%252F`가 되는 사고를 막는다.
    """
    raw = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not raw:
        sys.exit("DATA_GO_KR_KEY 환경변수가 없습니다.")
    return quote(unquote(raw), safe="")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "shakeit-schedule-bot"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def iso(yyyymmdd: str) -> str | None:
    """`20260314` → `2026-03-14`. 빈 값이면 None."""
    s = (yyyymmdd or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def fetch_sessions(key: str, year: int) -> list[dict]:
    url = (
        f"{SCHEDULE_URL}?serviceKey={key}&numOfRows={PAGE_SIZE}&pageNo=1"
        f"&dataFormat=json&implYy={year}&qualgbCd={QUAL_GB_CD}&jmCd={JM_CD}"
    )
    body = json.loads(get(url).decode("utf-8"))
    header = body.get("header", {})
    if header.get("resultCode") != "00":
        raise RuntimeError(f"{year}년 일정 조회 실패: {header}")

    items = body.get("body", {}).get("items") or []

    # 같은 회차가 여러 줄로 온다. 필기 원서접수가 정기/빈자리로 나뉘어
    # 각각 한 줄씩 오기 때문인데, 우리가 쓰는 실기 날짜는 두 줄이 같다.
    # 회차를 키로 합쳐 한 줄로 만든다.
    by_round: dict[int, dict] = {}
    skipped: list[int] = []

    for it in items:
        seq = int(it.get("implSeq", -1))

        # 제0회는 필기 날짜가 전부 비어 있고 실기 기간이 정기 회차와
        # 겹친다(2026년: 6/13~6/24가 2회 5/30~6/14와 물린다). 일반
        # 응시자용 회차가 아니므로 뺀다 — 넣으면 "다음 시험"이 겹쳐
        # 계산돼 엉뚱한 회차를 가리킨다.
        if seq <= 0:
            skipped.append(seq)
            continue

        start, end = iso(it.get("pracExamStartDt")), iso(it.get("pracExamEndDt"))
        apply_start = iso(it.get("pracRegStartDt"))
        apply_end = iso(it.get("pracRegEndDt"))
        # 실기 날짜가 없는 줄은 우리에게 쓸모가 없다.
        if not (start and end and apply_start and apply_end):
            continue

        by_round[seq] = {
            "year": year,
            "round": seq,
            "apply_start": apply_start,
            "apply_end": apply_end,
            "practical_start": start,
            "practical_end": end,
            "pass_date": iso(it.get("pracPassDt")),
        }

    if skipped:
        print(f"  {year}년: 특별회차 {sorted(set(skipped))} 제외")
    return [by_round[k] for k in sorted(by_round)]


def _text(node, *names) -> str | None:
    """항목에서 이름 후보 중 먼저 잡히는 값을 꺼낸다."""
    for n in names:
        el = node.find(n)
        if el is not None and (el.text or "").strip():
            return el.text.strip()
    return None


def fetch_pass_rates(key: str, years: list[int]) -> list[dict]:
    """지난 회차 합격률.

    **이 API의 응답 필드는 아직 실물로 확인하지 못했다.** 활용신청이
    승인되기 전에는 `NORMAL SERVICE`에 `totalCount=0`만 돌아온다.
    그래서 필드명을 여러 후보로 두고 읽으며, 못 읽으면 그 해를 조용히
    건너뛴다 — 합격률은 부가 정보라 없다고 일정까지 막으면 안 된다.
    승인 후 첫 실행 로그에서 실제 필드명을 확인하고 정리할 것.
    """
    out: list[dict] = []
    for year in years:
        # 이 API는 종목을 요청으로 좁힐 수 없어 전 종목이 섞여 온다.
        # 한 페이지가 50건이니 조주기능사를 만나려면 넘겨봐야 한다.
        items: list = []
        try:
            page = 1
            while page <= MAX_PAGES:
                url = (
                    f"{PASS_RATE_URL}?serviceKey={key}&baseYY={year}"
                    f"&numOfRows={PAGE_SIZE}&pageNo={page}"
                )
                root = ET.fromstring(get(url).decode("utf-8"))
                got = root.findall(".//item")
                items.extend(got)
                if len(got) < PAGE_SIZE:
                    break
                page += 1
            else:
                # 끝을 못 보고 상한에 걸렸다. 조용히 자르면 "합격률이
                # 없는 해"처럼 보이므로 드러낸다.
                print(f"  합격률 {year}년: {MAX_PAGES}페이지 상한에 걸림 — 뒤쪽 누락 가능")
        except (urllib.error.URLError, ET.ParseError, OSError) as e:
            print(f"  합격률 {year}년 조회 실패({type(e).__name__}) — 건너뜀")
            continue

        if not items:
            print(f"  합격률 {year}년: 0건 (활용신청 승인 대기로 보임)")
            continue

        before = len(out)
        for it in items:
            name = _text(it, "jmNm", "jmfldnm", "seriesnm") or ""
            if "조주" not in name:
                continue
            # 실기만 쓴다. 필기 합격률은 조주기능사 학습과 상관이 적다.
            gb = _text(it, "examgbNm", "examGbNm", "gbNm") or ""
            if gb and "실기" not in gb:
                continue
            try:
                applied = int(_text(it, "susiCnt", "applCnt", "rcptCnt") or 0)
                passed = int(_text(it, "passCnt", "passNum") or 0)
            except ValueError:
                continue
            if applied <= 0:
                continue
            out.append({
                "year": year,
                "round": int(_text(it, "implSeq", "seq") or 0),
                "applied": applied,
                "passed": passed,
                "rate": round(passed * 100 / applied, 1),
            })

        # 전 종목 수백 건을 받아놓고 조주기능사를 한 건도 못 골랐다면
        # 필드명 추측이 틀린 것이다. 그냥 넘어가면 "합격률이 원래 없다"로
        # 오해하게 되므로 로그에 남긴다.
        if len(out) == before:
            print(f"  합격률 {year}년: {len(items)}건 받았으나 조주기능사 0건 "
                  f"— 필드명 확인 필요")

    out.sort(key=lambda r: (r["year"], r["round"]))
    return out


def main() -> None:
    key = service_key()
    today = date.today()
    # 올해와 내년. 내년 일정이 공고되는 즉시 딸려 들어와서, 연말에
    # "일정 소진" 상태로 떨어지는 일이 없다.
    years = [today.year, today.year + 1]

    sessions: list[dict] = []
    for y in years:
        try:
            got = fetch_sessions(key, y)
            print(f"  {y}년 일정 {len(got)}회차")
            sessions.extend(got)
        except (urllib.error.URLError, RuntimeError, ValueError, OSError) as e:
            # 내년 일정은 아직 없는 게 정상이다. 올해가 실패하면 문제다.
            if y == today.year:
                raise
            print(f"  {y}년 일정 없음({type(e).__name__}) — 정상")

    if not sessions:
        sys.exit("일정을 한 건도 받지 못했습니다. 기존 파일을 그대로 둡니다.")

    rates = fetch_pass_rates(key, [today.year - 1, today.year])

    payload = {
        "source": "data.go.kr 국가자격 시험일정 / 국가기술자격 합격률",
        "jm_cd": JM_CD,
        "sessions": sessions,
        "pass_rates": rates,
    }

    # 일정이 안 바뀐 날에도 `generated_at` 때문에 파일이 달라지면 매일
    # 의미 없는 커밋이 쌓인다. 날짜를 뺀 알맹이가 같으면 손대지 않는다.
    if os.path.exists(OUT_PATH):
        with open(OUT_PATH, encoding="utf-8") as f:
            old = json.load(f)
        if {k: v for k, v in old.items() if k != "generated_at"} == payload:
            print("변경 없음 — 파일 그대로 둡니다.")
            return

    payload["generated_at"] = today.isoformat()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    # 키를 정렬해 항상 같은 바이트가 나오게 한다.
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    print(f"저장: {OUT_PATH} (일정 {len(sessions)}건, 합격률 {len(rates)}건)")


if __name__ == "__main__":
    main()
