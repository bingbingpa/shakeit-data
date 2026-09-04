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
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from urllib.parse import quote, unquote

# 조주기능사 종목코드. 큐넷 종목 페이지 URL의 jmCd 값이다.
JM_CD = "7916"

# 국가기술자격. 일정 API의 qualgbCd.
QUAL_GB_CD = "T"

# 기능사. 합격률 API의 grdCd(필수, 2자리). 10·20·30·40 체계이고 40이
# 기능사다 — 1~8을 넣으면 오류 없이 0건만 돌아와 승인 대기로 오해하기 쉽다.
GRD_CD = "40"

SCHEDULE_URL = "https://apis.data.go.kr/B490007/qualExamSchd/getQualExamSchdList"
PASS_RATE_URL = (
    "http://openapi.q-net.or.kr/api/service/rest/InquiryQualPassRateSVC/getList"
)

OUT_PATH = os.environ.get("OUT_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "exam_schedule.json",
)

# GitHub 러너(해외)에서 data.go.kr 응답이 들쭉날쭉하다. 같은 요청이
# 4초에 끝나기도 하고 30초를 넘기기도 한다.
TIMEOUT = 60

# 한 번 삐끗했다고 그날 갱신을 통째로 날리지 않는다. 매일 도는 작업이라
# 재시도가 없으면 빨간 X가 수시로 뜨고, 그러다 진짜 고장에 무뎌진다.
RETRIES = 3
RETRY_BACKOFF = 5  # 초. 시도마다 5, 10초 쉰다.

# 포털이 한 페이지 50건을 넘기면 resultCode 930으로 거절한다.
PAGE_SIZE = 50

# 합격률은 그 등급 전 종목이 섞여 온다(연 1,500건 안팎). 이 API는 페이지
# 크기에 제한이 없어 한 번에 다 받는다. 종목이 늘어도 버티도록 여유를 둔다.
PASS_RATE_PAGE_SIZE = 5000


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
    """느리면 몇 번 더 두드려본다.

    HTTP 오류(4xx·5xx)는 재시도하지 않는다. 키가 틀렸거나 파라미터가
    잘못된 것이라 다시 보내도 같은 답이 오고, 하루 쿼터만 축낸다.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "shakeit-schedule-bot"})
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == RETRIES:
                raise
            wait = RETRY_BACKOFF * attempt
            print(f"  연결 실패({type(e).__name__}) — {wait}초 후 재시도 "
                  f"{attempt}/{RETRIES - 1}")
            time.sleep(wait)
    raise RuntimeError("도달할 수 없는 분기")


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


def _text(node, tag) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def fetch_pass_rates(key: str, years: list[int]) -> list[dict]:
    """지난 회차 실기 합격률.

    **`grdCd`는 필수이고 값은 2자리다.** 처음에 1~8을 넣어보고 전부
    `totalCount=0`이 나와 "활용신청 승인 대기"로 오해했는데, 실제 체계는
    10·20·30·40이었다. 40이 기능사다.

    종목을 요청으로 좁힐 수 없어 그 등급의 전 종목이 섞여 온다(2024년
    기능사 1537건). 그래서 `totalCount`를 보고 끝까지 넘긴 뒤 조주기능사
    (jmCd 7916)만 골라낸다 — 이름으로 거르면 표기가 조금만 달라져도
    놓친다.

    실패해도 그 해를 건너뛸 뿐 일정 수집은 계속한다. 합격률은 부가
    정보라, 없다고 일정까지 막으면 안 된다.
    """
    out: list[dict] = []
    for year in years:
        try:
            items = _fetch_all_pages(key, year)
        except (urllib.error.URLError, ET.ParseError, OSError) as e:
            print(f"  합격률 {year}년 조회 실패({type(e).__name__}) — 건너뜀")
            continue

        if not items:
            print(f"  합격률 {year}년: 0건")
            continue

        before = len(out)
        for it in items:
            if _text(it, "jmCd") != JM_CD:
                continue
            # 실기만 쓴다. 필기 합격률은 실기 연습과 상관이 적다.
            if _text(it, "examTypCcd") != "실기":
                continue
            # 제0회는 일정에서도 빼고 있다(정기 회차와 기간이 겹친다).
            # 여기서만 남기면 화면에서 짝이 맞는 회차를 못 찾는다.
            try:
                seq = int(_text(it, "implSeq") or 0)
                applied = int(_text(it, "recptNoCnt") or 0)
                passed = int(_text(it, "examPassCnt") or 0)
            except ValueError:
                continue
            if seq < 1 or applied <= 0:
                continue
            out.append({
                "year": year,
                "round": seq,
                "applied": applied,
                "passed": passed,
                # passRate는 "59.4%" 같은 문자열로 온다. 우리가 다시
                # 계산하지 않고 그대로 쓴다 — 반올림 방식이 달라지면
                # 공단 발표와 숫자가 어긋난다.
                "rate": _parse_rate(_text(it, "passRate"), passed, applied),
            })

        got = len(out) - before
        if got == 0:
            print(f"  합격률 {year}년: {len(items)}건 중 조주기능사 실기 0건")
        else:
            print(f"  합격률 {year}년: {got}회차")

    out.sort(key=lambda r: (r["year"], r["round"]))
    return out


def _fetch_all_pages(key: str, year: int) -> list:
    """그 해 전 종목을 **한 번에** 받는다.

    종목을 요청으로 좁힐 수 없어(jmCd를 넣어도 무시된다) 그 등급 전체를
    받아 걸러야 한다. 다행히 이 API는 한 페이지 크기에 제한이 없다 —
    일정 API가 50을 넘기면 거절하길래 같은 줄 알고 50씩 31번 넘기고
    있었는데, 여기서는 한 번이면 된다. 하루 쿼터가 1,000회라 요청 수를
    아끼는 편이 낫다.
    """
    url = (
        f"{PASS_RATE_URL}?serviceKey={key}&baseYY={year}"
        f"&grdCd={GRD_CD}&numOfRows={PASS_RATE_PAGE_SIZE}&pageNo=1"
    )
    root = ET.fromstring(get(url).decode("utf-8"))
    items = root.findall(".//item")

    # 한 번에 다 못 받았으면 조용히 넘어가지 않는다 — 뒤쪽에 조주기능사가
    # 있으면 "합격률이 없는 해"처럼 보인다.
    el = root.find(".//totalCount")
    total = int(el.text) if el is not None and el.text else 0
    if total and len(items) < total:
        print(f"  합격률 {year}년: {len(items)}/{total}건만 받음 — "
              f"PASS_RATE_PAGE_SIZE를 늘릴 것")
    return items


def _parse_rate(raw: str, passed: int, applied: int) -> float:
    """`"59.4%"` → `59.4`. 값이 이상하면 직접 계산한다."""
    try:
        return round(float(raw.replace("%", "").strip()), 1)
    except ValueError:
        return round(passed * 100 / applied, 1) if applied else 0.0


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

    rates = fetch_pass_rates(key, [today.year - 2, today.year - 1, today.year])

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
