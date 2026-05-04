# CLAUDE.md
이 파일은 Claude Code가 이 프로젝트를 작업할 때 참고하는 설명서입니다.

---

## 1. 프로젝트 기본 설명

**명지대학교 학생용 AI 비서 서비스의 백엔드 서버입니다.**

학생이 앱에서 "내가 졸업하려면 뭐 더 들어야 해?", "다음 학기에 뭐 들으면 좋아?" 같은 걸 물어보면 답해주는 서비스의 서버 부분.

### 기술 스택
- **언어**: Python
- **프레임워크**: Django 5.2 + Django REST Framework (DRF)
  - Django: 웹 서버 만드는 도구
  - DRF: API(앱이 서버랑 통신할 때 쓰는 통로) 만드는 라이브러리
    - Django 기반 RESTful API를 쉽고 빠르게 구축할 수 있는 강력한 파이썬 라이브러리
- **DB**: SQLite (개발용 가벼운 DB. 나중에 운영은 PostgreSQL로 바꿀 예정일 수도)

### 프로젝트 구조
- 프로젝트 이름: `CapstoneDesign`
- 앱 3개:
  - **accounts**: 회원가입, 로그인, 프로필 관리
  - **courses**: 과목 정보, 졸업요건, 추천 기능
  - **common**: 여러 앱이 공통으로 쓰는 코드

### 작성 규칙
- **모두 한국어로 작성** (모델 이름, 응답 메시지, 주석 전부)

---

## 2. 자주 쓰는 명령어 (PowerShell 기준)

```powershell
# === 첫날 한 번만 ===
pip install -r requirements.txt          # 패키지 설치
python manage.py migrate                 # DB 초기화
python manage.py createsuperuser         # 관리자 계정 만들기

# === 작업 시작 ===
python manage.py runserver               # 서버 띄워놓고 (터미널 1)

# === 다른 터미널에서 (터미널 2) ===
# Claude Code랑 같이 모델 수정 작업...

python manage.py makemigrations courses  # 마이그레이션 파일(변경 계획서) 생성
python manage.py migrate                 # 마이그레이션을 실제 DB에 적용
python manage.py test courses            # courses 앱만 테스트만 돌리기

# 다 잘되면 git commit
```

### 서버 띄운 후 확인할 곳
- **API 문서**: `http://127.0.0.1:8000/api/docs/` ← Swagger UI로 모든 API 확인 가능
- API 스키마(원본): `http://127.0.0.1:8000/api/schema/`

---

## 3. accounts 앱 (회원/인증) — 친구가 만든 거

### User 모델 (회원 정보)
일반 Django User랑 다른 점:
- `username` 필드 없음. **이메일로 로그인**함
- 학생 정보 추가됨: 이름, 학년, 학기, 입학년도, 졸업예정 년/월, 전공
- 이메일 인증 여부, 온보딩 완료 여부, 카카오 로그인 ID
- 알림 설정 4개 (켜고 끄는 토글)

### 인증 방식: JWT
- 로그인하면 토큰(token) 2개 받음: access token + refresh token
- access token: API 호출할 때 헤더에 넣어서 보냄 (단기 토큰)
- refresh token: access 만료되면 새로 받을 때 쓰는 거 (장기 토큰)
- **로그아웃하면 access token도 즉시 무효화됨** (캐시에 차단 목록 저장)

> ⚠️ 운영 배포 시 주의: 캐시를 Redis 같은 공유 저장소로 바꿔야 함. 안 바꾸면 서버 여러 개 띄울 때 로그아웃이 한 서버에서만 적용됨.

### 이메일 인증 코드
- 회원가입할 때 이메일로 인증 코드 보냄
- `EmailVerification` 모델에 저장
- 코드는 `secrets.randbelow`로 생성 (예측 불가능한 안전한 난수)
- 비밀번호 재설정도 이메일 인증 코드 사용 (2단계: 인증 → 새 비번 설정)

### 보안 장치
- **분당 5회 제한**: 같은 이메일로 5번 이상 시도하면 막음 (해킹 방지)
- **enumeration 방지**: 회원가입/비번재설정 시 "이 이메일 있어요/없어요" 절대 안 알려줌. 무조건 "메일 보냈습니다"로 응답 (해커가 이메일 추측 못 하게)
- **timing oracle 방지**: 로그인 실패 시 존재하는 계정이든 아니든 같은 시간 걸리게 처리

> 형님이 새 코드 추가할 때 위 패턴들 그대로 따라 하기

---

## 4. 동시성 처리 (중요!)

여러 사용자가 동시에 같은 데이터 건드릴 때 꼬이지 않게 처리하는 패턴.

### 어디에 적용돼있나
- 회원가입 + 인증메일 발송
- 비밀번호 변경 + 토큰 차단
- 인증 코드 검증/사용
- 관심분야 등록 (사용자당 최대 3개 제한)

### 패턴
```python
# 같은 데이터 동시 수정 막기
with transaction.atomic():
    user = User.objects.select_for_update().get(...)
    # 처리
```

> 형님이 새 기능 추가할 때 동시 접근이 문제될 만하면 이 패턴 쓰기

> ⚠️ SQLite는 이거 완벽 지원 안 해서 가끔 에러 남. `verify_code` 함수에서 처리하는 패턴 참고.

---

## 5. URL 구조

API 주소가 어떻게 생겼냐:

### accounts 관련
`/api/v1/accounts/` 아래에:
- `signup/` 회원가입
- `verify-email/` 이메일 인증
- `login/` 로그인
- `kakao-login/` 카카오 로그인
- `token/refresh/` 토큰 갱신
- `logout/` 로그아웃
- `password/reset/request/` `verify/` `confirm/` 비번 재설정 3단계
- `profile/` 프로필
- `settings/` 설정
- `withdraw/` 회원탈퇴
- `interests/` 관심분야 (CRUD)
- `course-history/` 과거 수강내역 (CRUD)
- `current-courses/` 현재 수강중 (CRUD)

### courses 관련 ← **형님이 작업할 영역**
`/api/v1/courses/` 아래에:
- 과목 검색
- 이수현황 분석
- 다음학기 추천
- 커리큘럼 추천

> ⚠️ 형님이 새 기능 추가할 때 ViewSet 만들면, **반드시 `request.user`로 필터링**하고 `perform_create`에서 user 자동 주입할 것. 안 그러면 다른 사람 데이터 보임.

---

## 6. courses 앱 (수강/졸업) — 형님이 작업할 영역

### 모델 5개
- **Course**: 과목 마스터 정보 (학교 전체 과목 목록)
- **CoursePrerequisite**: 선수과목 관계 ("자료구조 들으려면 프로그래밍기초 먼저")
- **CourseSchedule**: 시간표 (요일/시간/강의실)
- **GraduationRequirement**: 졸업 요건 (학과/입학연도/카테고리별)
- **AcademicCalendar**: 학사일정

### 중요한 분리 규칙
- `accounts.CourseHistory` / `CurrentCourse` = **사용자가 들었던/듣는 과목** (개인 데이터)
- `courses.Course` = **학교 과목 마스터** (전체 데이터)
- 둘 사이 연결은 **`course_code`(과목 코드)로 매칭**

> ⚠️ 이거 헷갈려서 한쪽에 다 몰아넣으면 안 됨

### 추천 로직
- `NextSemesterRecommendView`: 다음학기 추천
- `CurriculumRecommendView`: 전체 커리큘럼 추천
- 작동 방식: `course.name`(과목 이름)으로 비교해서 이미 들은 과목 빼고, 선수과목 만족하는지 체크

> ⚠️ 추천 로직 수정할 때 이 비교 방식 깨지지 않게 조심

---

## 7. 환경 설정

### `.env` 파일에 들어가는 것 (Git에 올리면 안 됨)
- `SECRET_KEY`: Django 보안키
- `DEBUG`: 개발모드 여부 (True/False)
- `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`: 이메일 발송용 계정
- `EMAIL_BACKEND`: 이메일 발송 방식

### 로컬 개발 팁
- `EMAIL_HOST_USER` 비워두면 → 인증 코드가 **콘솔(터미널)에 그냥 출력**됨
- 즉, 로컬에서는 이메일 설정 안 해도 인증 테스트 가능

### 권한 기본값
- 모든 API는 **로그인 필요**가 기본값
- 로그인 없이 접근해야 하는 API는 `@permission_classes([AllowAny])` 명시 필요

### 페이지네이션
- 한 페이지에 20개씩, 최대 100개 (`common.pagination.StandardPagination`)

---

## 8. 참고 문서

- **`spec.md`** — 전체 API 명세서. **가장 권위 있는 문서. 헷갈리면 여기 봐.**
- `usecase.md` — 유스케이스 (어떤 상황에서 어떻게 쓰이는지)
- `server_architecture.md`, `server_architecture_v.2.0.md` — 발표용 다이어그램

---

## 9. Claude한테 부탁하는 것 (형님 작업 시 가이드)

- 새 코드 짤 때는 **위에 있는 보안/동시성 패턴 그대로 따라하기**
- 응답 메시지는 한국어로
- 사용자 데이터 다루는 ViewSet은 `request.user`로 필터링 필수
- 헷갈리면 `spec.md` 먼저 확인