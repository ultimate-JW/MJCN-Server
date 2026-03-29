# MJCN - 명지대학교 학생 AI 비서 서비스

> 명지대학교 캡스톤디자인 프로젝트
> 최종 수정일: 2026-03-29
> 기능명세서 v1.0 기반

---

## 1. 프로젝트 개요

### 1.1 목적

명지대학교 학생들의 학사 생활을 통합 지원하는 AI 기반 비서 서비스.
공지사항, 수강/졸업 관리, 공모전 정보를 하나의 플랫폼에서 제공하고,
AI 챗봇("띵똥이")을 통해 개인화된 답변과 PUSH 알림을 제공한다.

### 1.2 핵심 가치

- **통합**: 흩어진 학사 정보(공지, 수강, 공모전)를 한 곳에서 조회
- **개인화**: 사용자 프로필(전공, 학년, 관심분야) 기반 맞춤 추천
- **AI 비서**: 자연어 대화를 통한 즉각적 정보 제공

### 1.3 대상 사용자

- 명지대학교 재학생 (학부생 중심)
- 수강신청, 졸업요건, 공모전 등의 정보가 필요한 학생

---

## 2. 기술 스택

| 구분 | 기술 | 비고 |
|------|------|------|
| Language | Python 3.11 | |
| Framework | Django 5.2.12 + DRF | REST API 서버 |
| API | Django REST Framework | JSON API |
| Database | SQLite3 (개발) / PostgreSQL (운영) | |
| AI | OpenAI API | LLM 기반 챗봇 |
| 인증 | DRF Token 또는 JWT (SimpleJWT) | Custom User 모델 |
| PUSH 알림 | FCM (Firebase Cloud Messaging) | 안드로이드 PUSH 전송 |
| 비동기 작업 | Django-Q2 또는 Celery + Redis | 크롤링/알림 스케줄링 |
| 캐시 | Redis (운영) | 선택 |
| 파일 저장 | Django FileField / S3 (운영) | 첨부파일용 |
| 문서화 | drf-spectacular (Swagger/OpenAPI) | API 문서 자동 생성 |
| CORS | django-cors-headers | 프론트엔드 연동용 |

> **NOTE**: 이 프로젝트는 백엔드 REST API만 담당합니다.
> 프론트엔드는 별도 팀원이 개발합니다: **Android (Kotlin)** + **iOS (Swift)**

---

## 3. Django 앱 구조

```
CapstoneDesign/              # 프로젝트 설정 (settings, urls, wsgi)
├── accounts/                # 회원가입, 로그인, 프로필 관리
│   ├── models.py            # User, InterestArea, CourseHistory, CurrentCourse
│   ├── serializers.py       # 회원가입/프로필/설정 Serializer
│   ├── views.py             # API ViewSet
│   └── urls.py
├── chat/                    # AI 비서 채팅 (띵똥이)
│   ├── models.py            # ChatRoom, ChatMessage, ChatAttachment
│   ├── serializers.py
│   ├── views.py
│   ├── services.py          # AI API 호출, 카테고리 분류 로직
│   └── urls.py
├── courses/                 # 수강/졸업 관리, 과목 추천
│   ├── models.py            # Course, CoursePrerequisite, GraduationRequirement
│   ├── serializers.py
│   ├── views.py
│   ├── services.py          # 추천 알고리즘, 이수현황 계산
│   └── urls.py
├── notices/                 # 공지사항 통합 조회
│   ├── models.py            # Notice
│   ├── serializers.py
│   ├── views.py
│   ├── crawlers.py          # 크롤러
│   └── urls.py
├── contests/                # 공모전 통합 조회
│   ├── models.py            # Contest
│   ├── serializers.py
│   ├── views.py
│   ├── crawlers.py          # 크롤러
│   └── urls.py
├── notifications/           # PUSH 알림
│   ├── models.py            # Notification
│   ├── serializers.py
│   ├── views.py
│   ├── services.py          # 알림 생성/스케줄링 로직
│   └── urls.py
├── dashboard/               # 메인화면 데이터 집계 API
│   ├── views.py
│   └── urls.py
├── common/                  # 공통 유틸, 미들웨어, 권한 클래스
│   ├── permissions.py       # 커스텀 DRF 권한
│   ├── pagination.py        # 공통 페이지네이션
│   └── mixins.py
└── media/                   # 업로드 파일
```

### 앱별 책임

| 앱 | 책임 | 기능명세서 항목 |
|-----|------|----------------|
| `accounts` | 회원가입, 인증(JWT), 프로필 CRUD, 설정, 탈퇴 | 1, 5(설정) |
| `chat` | AI 대화 API, 채팅방 보관함, 폴더 분류 | 2.1, 5(보관함) |
| `courses` | 수강과목 추천 API, 커리큘럼, 이수현황 분석 | 3 |
| `notices` | 공지사항 크롤링/저장, 통합 조회 API, 검색 | 4.1 |
| `contests` | 공모전 크롤링/저장, 통합 조회 API | 4.2 |
| `notifications` | 알림 생성, 조회 API, 읽음 처리, 스케줄링 | 2.2, 7 |
| `dashboard` | 메인화면 데이터 집계 API (시간표, 공지, 공모전 등) | 6 |
| `common` | 공통 권한, 페이지네이션, mixin, 유틸 | - |

---

## 4. 데이터 모델

### 4.1 accounts 앱

#### User (AbstractUser 확장)

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| email | EmailField | O | 로그인 ID (USERNAME_FIELD) |
| name | CharField(50) | O | 실명 |
| major | CharField(100) | O | 전공 |
| grade | IntegerField | O | 학년 (1~4) |
| semester | IntegerField | O | 학기 (1 or 2) |
| is_email_verified | BooleanField | O | 이메일 인증 여부 |
| notification_enabled | BooleanField | O | 알림 수신 여부 (기본 True) |

#### InterestArea (관심분야)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| category | CharField | 직업군 선택형 (choices) |
| custom_text | TextField(blank) | 자유 텍스트 입력 |

#### CourseHistory (수강이력)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| course_name | CharField | 과목명 |
| course_code | CharField | 학수번호 |
| year | IntegerField | 수강 연도 |
| semester | IntegerField | 수강 학기 |
| grade_received | CharField(blank) | 취득 성적 |
| category | CharField | 전공필수/전공선택/교양필수/교양선택/일반선택 |
| credits | IntegerField | 학점 수 |

#### CurrentCourse (현재 수강과목)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| course_name | CharField | 과목명 |
| course_code | CharField | 학수번호 |
| day_of_week | CharField | 요일 |
| start_time | TimeField | 시작 시간 |
| end_time | TimeField | 종료 시간 |
| room | CharField(blank) | 강의실 |
| professor | CharField(blank) | 교수명 |

#### EmailVerification

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| token | UUIDField | 인증 토큰 |
| created_at | DateTimeField | 생성 시각 |
| is_used | BooleanField | 사용 여부 |

### 4.2 chat 앱

#### ChatRoom (채팅방)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| title | CharField | 채팅방 제목 (자동 생성) |
| category | CharField | 자동 분류 (공지/공모전/취업·진로/취미/기타) |
| created_at | DateTimeField | |
| updated_at | DateTimeField | |

#### ChatMessage (채팅 메시지)

| 필드 | 타입 | 설명 |
|------|------|------|
| room | FK(ChatRoom) | |
| role | CharField | "user" 또는 "assistant" |
| content | TextField | 메시지 내용 |
| created_at | DateTimeField | |

#### ChatAttachment (첨부파일)

| 필드 | 타입 | 설명 |
|------|------|------|
| message | FK(ChatMessage) | |
| file | FileField | 업로드 파일 |
| file_type | CharField | image/video/document |
| original_name | CharField | 원본 파일명 |

### 4.3 courses 앱

#### Course (과목 정보 - 학교 데이터)

| 필드 | 타입 | 설명 |
|------|------|------|
| course_code | CharField(unique) | 학수번호 |
| name | CharField | 과목명 |
| department | CharField | 개설 학과 |
| category | CharField | 전공필수/전공선택/교양필수/교양선택 |
| credits | IntegerField | 학점 |
| year | IntegerField | 개설 연도 |
| semester | IntegerField | 개설 학기 |
| day_of_week | CharField(blank) | 요일 |
| start_time | TimeField(null) | 시작 시간 |
| end_time | TimeField(null) | 종료 시간 |
| room | CharField(blank) | 강의실 |
| professor | CharField(blank) | 교수명 |

#### CoursePrerequisite (선후수 관계)

| 필드 | 타입 | 설명 |
|------|------|------|
| course | FK(Course) | 대상 과목 |
| prerequisite | FK(Course) | 선수 과목 |

#### GraduationRequirement (졸업요건)

| 필드 | 타입 | 설명 |
|------|------|------|
| department | CharField | 학과 |
| admission_year | IntegerField | 입학 연도 |
| category | CharField | 전공필수/전공선택/교양필수/... |
| required_credits | IntegerField | 필요 학점 |
| total_required | IntegerField | 총 졸업 학점 |

### 4.4 notices 앱

#### Notice (공지사항)

| 필드 | 타입 | 설명 |
|------|------|------|
| source | CharField | 출처 (학사공지/일반공지/행사공지/장학공지) |
| title | CharField | 제목 |
| content | TextField | 내용 |
| url | URLField | 원문 링크 |
| published_at | DateTimeField | 게시일 |
| deadline | DateField(null) | 마감일 (있는 경우) |
| created_at | DateTimeField | 수집 시각 |
| tags | JSONField(default=list) | 자동 태깅 키워드 |

### 4.5 contests 앱

#### Contest (공모전)

| 필드 | 타입 | 설명 |
|------|------|------|
| title | CharField | 제목 |
| organizer | CharField | 주최 |
| description | TextField | 설명 |
| url | URLField | 원문 링크 |
| deadline | DateField | 마감일 |
| categories | JSONField(default=list) | 분야 태그 |
| created_at | DateTimeField | 수집 시각 |

### 4.6 notifications 앱

#### Notification (알림)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| title | CharField | 알림 제목 |
| message | TextField | 알림 내용 |
| notification_type | CharField | notice/contest/course/system |
| related_id | IntegerField(null) | 관련 객체 ID (프론트에서 화면 이동용) |
| is_read | BooleanField | 읽음 여부 |
| is_pushed | BooleanField | FCM 전송 여부 |
| created_at | DateTimeField | |

#### FCMDevice (디바이스 토큰)

| 필드 | 타입 | 설명 |
|------|------|------|
| user | FK(User) | |
| registration_token | TextField | FCM 등록 토큰 |
| is_active | BooleanField | 활성 여부 |
| created_at | DateTimeField | |
| updated_at | DateTimeField | |

---

## 5. 기능 상세 명세

### 5.1 회원가입 / 인증 (accounts)

#### 5.1.1 이메일 가입

- 이메일 + 비밀번호로 가입
- 가입 후 인증 메일 발송 (UUID 토큰 링크)
- 인증 완료 전까지 로그인 불가

#### 5.1.2 프로필 설정 (가입 후 온보딩)

- **필수 입력**: 이름, 전공, 학년, 학기, 관심분야(선택형 1개 이상)
- **선택 입력**: 수강이력, 현재 수강과목, 관심분야(자유 텍스트)
- 관심분야 선택형 목록 (직업군 위주):
  - IT/개발, 디자인, 마케팅/광고, 금융/회계, 교육, 공공/행정,
    의료/바이오, 미디어/콘텐츠, 연구/R&D, 기타

#### 5.1.3 로그인 / 로그아웃

- 이메일 + 비밀번호 로그인
- JWT 토큰 발급 (access + refresh)
- access 토큰 만료 시 refresh 토큰으로 갱신

### 5.2 AI 비서 - 띵똥이 (chat)

#### 5.2.1 대화형 인터페이스

- 메시지 전송 API → AI 응답 반환
- 새 채팅방 생성 또는 기존 채팅방에 메시지 추가
- AI 컨텍스트: 사용자 프로필 + 대화 히스토리 + 학교 데이터

#### 5.2.2 텍스트 전송

- POST 요청으로 메시지 전송
- AI API 호출 → 응답 저장 → JSON 응답 반환

#### 5.2.3 첨부파일 전송

- 이미지, 동영상, 문서 첨부 가능
- 파일 업로드 후 AI에 함께 전달 (멀티모달 지원 시)
- 지원 형식: jpg/png/gif, mp4, pdf/docx
- 파일 크기 제한: 10MB

#### 5.2.4 정보 추천 (PUSH 알림) - notifications 앱과 연계

- **맞춤형 공지사항 추천**: 새 공지 등록시 + 마감일 전날
- **맞춤형 수강과목 추천**: 수강신청 공지 등록시 + 수강신청일 전날 + 미리담기 전날
- **맞춤형 공모전 추천**: 새 공모전 등록시 + 마감일 전날
- **교내 지원사업 능동 노출**: 홍보 부족 장학금/지원사업을 사용자 데이터 기반으로 선별하여 능동 알림
- 추천 로직: 사용자 관심분야/전공과 공지·공모전 태그 매칭, **관련도 상위 3개 이상 추천**

#### 5.2.5 상황별 가이딩 (학사 흐름 기반)

- 수강신청 시기, 학기 종료, 미리담기 등 주요 학사 이벤트 시점에 맞춤형 가이드 자동 제공
- 예: 수강신청 2주 전 → 추천 과목 알림, 학기 종료 → 다음학기 커리큘럼 제안
- 가이딩 트리거는 학사 일정 데이터 기반 스케줄링

### 5.3 수강/졸업 관리 (courses)

#### 5.3.1 다음학기 수강과목 추천

- 입력: 사용자 수강이력 + 전공 + 학년/학기
- 고려사항: 졸업요건 충족, 필수교양 이수, 선후수 과목
- 출력: 전공/교양 분리된 추천 목록
- 과목 정보: 과목명, 학수번호, 시간, 강의실, 교수명

#### 5.3.2 전체 커리큘럼 추천

- 현재 학기부터 졸업까지의 전체 수강 로드맵
- **2안 이상의 커리큘럼을 제시** (정량적 기준)
- 학기별 추천 과목 리스트

#### 5.3.3 이수현황 분석

- 카테고리별 이수학점 / 필요학점 표시
- 전공필수, 전공선택, 교양필수, 교양선택, 일반선택, 총학점
- 졸업까지 남은 학점 계산

### 5.4 통합 정보 제공 - 공지사항 (notices)

#### 5.4.1 전체보기

- 학사공지, 일반공지, 행사공지, 장학공지 통합 리스트
- 형식: `[출처] 제목`
- 검색 기능 (제목, 내용 검색)
- 페이지네이션

#### 5.4.2 맞춤형 보기 (기본값)

- 사용자 프로필(전공, 관심분야) 기반 필터링
- 전체보기 ↔ 맞춤형 보기 토글 전환

### 5.5 통합 정보 제공 - 공모전 (contests)

#### 5.5.1 전체보기

- 전체 공모전 리스트
- `D-NN 제목` 형식으로 마감일 표시

#### 5.5.2 맞춤형 보기 (기본값)

- 관심분야 기반 필터링
- 토글 전환

### 5.6 채팅방 보관함 (chat)

#### 5.6.1 전체 조회

- 사용자의 모든 채팅방 목록 (최신순)
- 각 항목: 제목, 마지막 메시지 미리보기, 날짜

#### 5.6.2 폴더별 조회

- 카테고리 자동 분류: 공지, 공모전, 취업/진로, 취미, 기타
- AI가 대화 내용 기반으로 자동 분류

#### 5.6.3 채팅 삭제

- 개별 채팅방 삭제

#### 5.6.4 채팅 이어가기

- 기존 채팅방 선택 → 대화 이어서 진행

### 5.7 설정 (accounts)

#### 5.7.1 알림 on/off

- 전체 알림 수신 토글

#### 5.7.2 프로필 수정

- 수정 가능: 학년/학기, 수강이력, 현재 수강과목, 관심분야
- 수정 불가: 이름, 전공
- 회원 탈퇴: 버튼 3회 탭으로 확인

### 5.8 메인화면 데이터 (dashboard)

> 단일 API 호출로 메인화면에 필요한 모든 데이터를 집계하여 반환

#### 응답 데이터 구성

- **greeting**: 인사 문구 데이터 (사용자명, 요일, 오늘 수업 수)
- **today_schedule**: 오늘 요일 기준 수업 리스트 (시간순 정렬)
- **notices**: 관심사 기반 최근 공지 N개
- **contests**: 관심사 기반 공모전 N개 (D-day 포함)
- **unread_notification_count**: 읽지 않은 알림 수

### 5.9 알림 (notifications)

#### 5.9.1 전체보기

- 전체 알림 리스트 API (최신순, 페이지네이션)
- 각 알림에 is_read 필드 포함
- related_url 필드로 프론트에서 이동할 경로 제공

---

## 6. REST API 설계

> 모든 API는 `/api/v1/` 접두사를 사용합니다.
> 인증이 필요한 API는 `Authorization: Bearer <access_token>` 헤더를 요구합니다.
> 응답 형식: JSON

### 6.1 인증 (accounts)

| Method | URL | 인증 | 설명 | 요청 body |
|--------|-----|------|------|-----------|
| POST | `/api/v1/accounts/signup/` | X | 회원가입 | `{email, password}` |
| POST | `/api/v1/accounts/verify-email/` | X | 이메일 인증 | `{token}` |
| POST | `/api/v1/accounts/login/` | X | 로그인 (JWT 발급) | `{email, password}` |
| POST | `/api/v1/accounts/token/refresh/` | X | 토큰 갱신 | `{refresh}` |
| POST | `/api/v1/accounts/logout/` | O | 로그아웃 (refresh 무효화) | `{refresh}` |

### 6.2 프로필 / 설정 (accounts)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/accounts/profile/` | O | 내 프로필 조회 |
| PUT | `/api/v1/accounts/profile/` | O | 프로필 전체 수정 (온보딩) |
| PATCH | `/api/v1/accounts/profile/` | O | 프로필 부분 수정 |
| GET | `/api/v1/accounts/settings/` | O | 설정 조회 (알림 on/off 등) |
| PATCH | `/api/v1/accounts/settings/` | O | 설정 수정 |
| DELETE | `/api/v1/accounts/withdraw/` | O | 회원 탈퇴 |

### 6.3 관심분야 (accounts)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/accounts/interests/` | O | 관심분야 목록 조회 |
| POST | `/api/v1/accounts/interests/` | O | 관심분야 추가 |
| DELETE | `/api/v1/accounts/interests/<id>/` | O | 관심분야 삭제 |

### 6.4 수강이력 / 현재수강 (accounts)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/accounts/course-history/` | O | 수강이력 목록 |
| POST | `/api/v1/accounts/course-history/` | O | 수강이력 추가 |
| PUT | `/api/v1/accounts/course-history/<id>/` | O | 수강이력 수정 |
| DELETE | `/api/v1/accounts/course-history/<id>/` | O | 수강이력 삭제 |
| GET | `/api/v1/accounts/current-courses/` | O | 현재 수강과목 목록 |
| POST | `/api/v1/accounts/current-courses/` | O | 현재 수강과목 추가 |
| PUT | `/api/v1/accounts/current-courses/<id>/` | O | 현재 수강과목 수정 |
| DELETE | `/api/v1/accounts/current-courses/<id>/` | O | 현재 수강과목 삭제 |

### 6.5 AI 채팅 (chat)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/chat/rooms/` | O | 채팅방 목록 (전체) |
| GET | `/api/v1/chat/rooms/?category=<cat>` | O | 채팅방 폴더별 조회 |
| POST | `/api/v1/chat/rooms/` | O | 새 채팅방 생성 |
| GET | `/api/v1/chat/rooms/<id>/` | O | 채팅방 상세 (메시지 히스토리) |
| DELETE | `/api/v1/chat/rooms/<id>/` | O | 채팅방 삭제 |
| POST | `/api/v1/chat/rooms/<id>/messages/` | O | 메시지 전송 + AI 응답 |
| POST | `/api/v1/chat/rooms/<id>/messages/` | O | 첨부파일 전송 (multipart) |

### 6.6 수강/졸업 관리 (courses)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/courses/recommend/next/` | O | 다음학기 수강과목 추천 |
| GET | `/api/v1/courses/recommend/curriculum/` | O | 전체 커리큘럼 추천 |
| GET | `/api/v1/courses/status/` | O | 이수현황 분석 |
| GET | `/api/v1/courses/` | O | 과목 검색 (쿼리 파라미터) |

### 6.7 공지사항 (notices)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/notices/` | O | 공지 목록 (맞춤형 기본) |
| GET | `/api/v1/notices/?view=all` | O | 공지 전체보기 |
| GET | `/api/v1/notices/?q=<검색어>` | O | 공지 검색 |
| GET | `/api/v1/notices/<id>/` | O | 공지 상세 |

### 6.8 공모전 (contests)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/contests/` | O | 공모전 목록 (맞춤형 기본) |
| GET | `/api/v1/contests/?view=all` | O | 공모전 전체보기 |
| GET | `/api/v1/contests/<id>/` | O | 공모전 상세 |

### 6.9 알림 (notifications)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/notifications/` | O | 알림 전체 목록 (페이지네이션) |
| GET | `/api/v1/notifications/unread-count/` | O | 읽지 않은 알림 수 |
| PATCH | `/api/v1/notifications/<id>/` | O | 읽음 처리 |
| POST | `/api/v1/notifications/read-all/` | O | 전체 읽음 처리 |
| POST | `/api/v1/notifications/devices/` | O | FCM 디바이스 토큰 등록/갱신 |
| DELETE | `/api/v1/notifications/devices/` | O | FCM 디바이스 토큰 삭제 (로그아웃 시) |

### 6.10 대시보드 (dashboard)

| Method | URL | 인증 | 설명 |
|--------|-----|------|------|
| GET | `/api/v1/dashboard/` | O | 메인화면 집계 데이터 |

### 6.11 API 문서

| Method | URL | 설명 |
|--------|-----|------|
| GET | `/api/docs/` | Swagger UI |
| GET | `/api/schema/` | OpenAPI 스키마 (JSON/YAML) |

### 6.12 공통 응답 형식

**성공 (단일)**
```json
{
  "id": 1,
  "field": "value"
}
```

**성공 (목록 + 페이지네이션)**
```json
{
  "count": 100,
  "next": "https://.../api/v1/notices/?page=2",
  "previous": null,
  "results": []
}
```

**에러**
```json
{
  "detail": "에러 메시지"
}
```

**유효성 검사 에러**
```json
{
  "email": ["이미 사용 중인 이메일입니다."],
  "password": ["비밀번호는 8자 이상이어야 합니다."]
}
```

---

## 8. 비기능 요구사항

### 8.1 보안

- JWT 토큰 기반 인증 (access 만료: 30분, refresh 만료: 7일)
- 비밀번호 해싱 (Django 기본 PBKDF2)
- 이메일 인증 필수
- SECRET_KEY 환경변수 분리 (`.env`)
- DEBUG=False (운영 환경)
- CORS 설정 (안드로이드 네이티브는 CORS 제약 없으나, 웹 디버깅/관리자용으로 유지)
- API Throttling 설정 (DRF 기본 제공)

### 8.2 성능 (정량적 기준)

- **일반 API 응답 시간**: 3초 이내
- **AI 채팅 응답 시간**: 8초 이내
- **AI 적절 응답률**: 80% 이상 (사용자 테스트 기준)
- **데이터 갱신 주기**: 주요 학사/공지 데이터 1일 1회 이상
- 공지사항/공모전 크롤링: 주기적 백그라운드 작업
- AI 응답: 스트리밍 응답 고려 (SSE 또는 polling)
- DB 인덱싱: 자주 조회되는 필드 (user, created_at, deadline 등)
- API 페이지네이션: 기본 20개, 최대 100개

### 8.3 확장성

- Custom User 모델 (프로젝트 초기부터 설정 필수)
- 앱 간 느슨한 결합 (FK 관계는 있되, 비즈니스 로직은 각 앱 내)
- Serializer / ViewSet / Service 계층 분리
- API 버전 관리 (`/api/v1/`)

### 8.4 데이터

- 공지사항/공모전: 주기적 크롤링으로 수집
- 과목/졸업요건 데이터: 초기 시딩 (fixture 또는 management command)
- 사용자 업로드 파일: `MEDIA_ROOT` 관리

---

## 9. 외부 연동

### 9.1 AI API (OpenAI)

- OpenAI API 사용
- 시스템 프롬프트에 사용자 프로필 정보 주입
- 대화 히스토리를 컨텍스트로 전달
- 채팅방 카테고리 자동 분류용 별도 호출
- 사용자 맞춤형 추천을 위한 프롬프트 설계 및 응답 최적화

### 9.2 크롤링 대상

- 명지대학교 공지사항 페이지 (학사, 일반, 장학)
- ~~알림톡 (DB 미확보로 보류, 추후 추가 가능)~~
- 공모전 사이트 (링커리어, 씽굿, 위비티 등)

### 9.3 FCM (Firebase Cloud Messaging)

- 안드로이드 앱으로 PUSH 알림 전송
- 백엔드에서 `firebase-admin` SDK 사용
- 앱 로그인 시 FCM 토큰을 서버에 등록, 로그아웃 시 삭제
- 알림 발생 시 해당 사용자의 활성 디바이스로 PUSH 전송

### 9.4 이메일 발송

- Django `send_mail` + SMTP 설정 (Gmail SMTP 또는 운영용 메일 서버)

---

## 10. 개발 단계

> 총 개발 기간: **10주**

### Phase 1 - 프로젝트 기반 + 인증 API (2주)

- [ ] DRF + SimpleJWT + drf-spectacular + django-cors-headers 설치
- [ ] settings 분리 (`base.py`, `dev.py`, `prod.py`) + `.env` 관리
- [ ] Custom User 모델 + accounts 앱 (`AUTH_USER_MODEL` 설정)
- [ ] 회원가입 / 이메일 인증 / 로그인(JWT) / 로그아웃 API
- [ ] 프로필 CRUD API (온보딩 + 수정)
- [ ] 관심분야 / 수강이력 / 현재수강 CRUD API
- [ ] 설정 API (알림 토글, 회원 탈퇴)
- [ ] 공통 권한, 페이지네이션, 에러 핸들링 설정

### Phase 2 - 데이터 수집 (2주)

- [ ] notices 앱: 공지사항 모델 + Serializer + 크롤러 + management command
- [ ] contests 앱: 공모전 모델 + Serializer + 크롤러
- [ ] courses 앱: 과목/졸업요건 모델 + Serializer + 시드 데이터
- [ ] 크롤링 스케줄러 설정

### Phase 3 - 정보 조회 API (2주)

- [ ] 공지사항 조회 API (전체/맞춤형, 검색, 페이지네이션)
- [ ] 공모전 조회 API (전체/맞춤형, 페이지네이션)
- [ ] 수강과목 추천 + 이수현황 분석 API
- [ ] 대시보드 집계 API

### Phase 4 - AI 비서 API (2주)

- [ ] chat 앱: 채팅방/메시지 모델 + Serializer
- [ ] AI API 연동 (services.py) + 메시지 전송 API
- [ ] 첨부파일 업로드 API (multipart)
- [ ] 채팅방 목록/폴더별 조회/삭제 API
- [ ] 채팅방 카테고리 자동 분류 로직

### Phase 5 - 알림 + 마무리 (2주)

- [ ] notifications 앱: 알림 모델 + 조회/읽음처리 API
- [ ] 맞춤 추천 알림 스케줄링 (마감일 전날 등)
- [ ] API 통합 테스트 + Swagger 문서 검증
- [ ] 운영 환경 설정 (PostgreSQL, 환경변수 등)
- [ ] 프론트엔드 팀과 API 연동 테스트

---

## 11. 초기 설정 체크리스트

프로젝트 시작 시 반드시 먼저 수행할 항목:

1. **`AUTH_USER_MODEL` 설정** - accounts 앱 생성 후 즉시 설정 (마이그레이션 전)
2. **settings.py 분리** - `base.py`, `dev.py`, `prod.py`
3. **환경변수 관리** - `python-dotenv` 또는 `django-environ` 도입
4. **LANGUAGE_CODE / TIME_ZONE** - `ko-kr`, `Asia/Seoul`로 변경
5. **`.gitignore`** - `.env`, `db.sqlite3`, `media/`, `__pycache__/` 등
6. **requirements.txt 핵심 패키지**:
   - `Django`, `djangorestframework`, `djangorestframework-simplejwt`
   - `drf-spectacular`, `django-cors-headers`
   - `python-dotenv`, `requests` (크롤링), `openai` (AI API)
   - `firebase-admin` (FCM PUSH 알림)
7. **DRF 기본 설정** - `DEFAULT_AUTHENTICATION_CLASSES`, `DEFAULT_PAGINATION_CLASS`, `DEFAULT_THROTTLE_RATES`
8. **CORS 설정** - `CORS_ALLOWED_ORIGINS`에 프론트엔드 도메인 등록
9. **Swagger 설정** - drf-spectacular `SPECTACULAR_SETTINGS` 구성
