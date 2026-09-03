# shakeit-data

[쉐킷쉐킷](https://github.com/bingbingpa/shakeit)이 읽어가는 공개 데이터.

앱 소스는 비공개지만 이 데이터는 공개다. 애초에 공공데이터포털이 공개한
시험 일정이라 숨길 것이 없고, 앱이 토큰 없이 받아가려면 공개여야 한다.

## exam_schedule.json

조주기능사(종목코드 7916) 실기 시험 일정과 지난 회차 합격률.

```
https://raw.githubusercontent.com/bingbingpa/shakeit-data/main/exam_schedule.json
```

```json
{
  "generated_at": "2026-09-03",
  "sessions": [
    {
      "year": 2026,
      "round": 3,
      "apply_start": "2026-07-27",
      "apply_end": "2026-08-24",
      "practical_start": "2026-08-29",
      "practical_end": "2026-09-16",
      "pass_date": "2026-10-02"
    }
  ],
  "pass_rates": []
}
```

`sessions`는 실기 기준이다. 조주기능사는 특정 하루가 아니라
`practical_start`~`practical_end` 기간 중 배정받은 날에 본다.

`pass_rates`는 지난 회차 실기 합격률이며, 발표 전이거나 API가 주지 않으면
비어 있다. **비어 있는 것이 정상 상태 중 하나다** — 없다고 일정까지 막지 않는다.

## 갱신

`.github/workflows/exam-schedule.yml`이 매일 05:00 KST에 돌면서
공공데이터포털을 조회하고, **내용이 달라졌을 때만** 커밋한다.

- [국가자격 시험일정](https://www.data.go.kr/data/15074408/openapi.do) — 회차·접수·시험 기간
- [국가기술자격 합격률](https://www.data.go.kr/data/15089380/openapi.do) — 응시자수·합격률

인증키는 저장소 시크릿 `DATA_GO_KR_KEY`로 넣는다. 코드에는 들어 있지 않다.

수동 실행은 Actions 탭의 `시험일정 갱신` → `Run workflow`.

## 왜 앱이 API를 직접 부르지 않나

개발계정 쿼터가 **1,000회/일**이다. 앱에서 직접 부르면 호출이 사용자 수에
비례해 늘어 500명만 넘어도 그날치가 소진되고, 그때부터는 **아무도** 일정을
못 본다. 게다가 키를 앱에 넣으면 디컴파일로 새어 나간다.

시험 일정은 1년에 몇 번 바뀌는 데이터다. 하루 한 번 여기서 받아 굳혀두면
호출은 하루 몇 번으로 고정되고, 키는 저장소 시크릿 밖으로 나가지 않는다.
