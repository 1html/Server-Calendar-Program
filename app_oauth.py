# app_oauth.py
# ---------------------------------------------
# 구글 캘린더 + OAuth 2.0 + 멀티유저(엘리스/밥) 동시 이벤트 등록
# ---------------------------------------------
import os, json
from flask import Flask, request, redirect, session
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# ===== 환경설정 =====
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret")  # 배포 시 환경변수로 바꾸세요
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")  # 배포 시 https://도메인 으로 설정

# 로컬 http 개발 시 OAuth 에러 방지 (배포에서는 제외)
if BASE_URL.startswith("http://"):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# client_secret 설정: 환경변수(GOOGLE_CLIENT_CONFIG_JSON) 우선, 없으면 파일 사용
if "GOOGLE_CLIENT_CONFIG_JSON" in os.environ:
    GOOGLE_CLIENT_CONFIG = json.loads(os.environ["GOOGLE_CLIENT_CONFIG_JSON"])
else:
    with open("client_secret.json", "r", encoding="utf-8") as f:
        GOOGLE_CLIENT_CONFIG = json.load(f)

# 토큰 저장 폴더(사용자별 파일)
TOKENS_DIR = "tokens"
os.makedirs(TOKENS_DIR, exist_ok=True)

def token_path(user): return os.path.join(TOKENS_DIR, f"{user}.json")

def save_credentials_for(user: str, creds: Credentials):
    with open(token_path(user), "w", encoding="utf-8") as f:
        f.write(creds.to_json())

def load_credentials_for(user: str):
    p = token_path(user)
    if not os.path.exists(p): return None
    return Credentials.from_authorized_user_file(p, SCOPES)

def build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds)

# (테스트용) 이름→이메일 매핑: 실제 이메일로 바꾸세요
NAME_TO_EMAIL = {
    "alice": "compass0303@naver.com",
    "bob"  : "kdhcompass0303@gmails.com",
}

# ===== 라우트 =====
@app.route("/")
def home():
    return (
        "<h2>Calendar Project</h2>"
        "<ul>"
        "<li><a href='/auth/me'>/auth/me (단일 사용자 로그인)</a></li>"
        "<li><a href='/auth/alice'>/auth/alice (엘리스 로그인)</a></li>"
        "<li><a href='/auth/bob'>/auth/bob (밥 로그인)</a></li>"
        "<li><a href='/whoami/me'>/whoami/me (내 캘린더 확인)</a></li>"
        "<li><a href='/whoami/alice'>/whoami/alice (엘리스 캘린더 확인)</a></li>"
        "<li><a href='/whoami/bob'>/whoami/bob (밥 캘린더 확인)</a></li>"
        "<li><a href='/make_test_event'>/make_test_event (단일 사용자 이벤트 생성)</a></li>"
        "<li><a href='/make_test_event_multi'>/make_test_event_multi (엘리스/밥 동시 생성)</a></li>"
        "<li><a href='/routes'>/routes (등록된 라우트 보기)</a></li>"
        "</ul>"
        f"<p>BASE_URL: {BASE_URL}</p>"
    )

# --- 단일 사용자(me) 로그인 & 콜백 ---
@app.route("/auth/me")
def auth_me():
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/me"
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["state"] = state
    return redirect(auth_url)

@app.route("/oauth2/callback/me")
def oauth2_callback_me():
    state = session.get("state")
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/me"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials_for("me", creds)

    service = build_service(creds)
    info = service.calendars().get(calendarId="primary").execute()
    return f"✅ me 연결 완료!<br>캘린더: {info.get('summary')}<br><a href='/'>홈</a>"

# --- 사용자별 로그인 & 콜백 (멀티유저) ---
@app.route("/auth/<user>")
def auth_user(user):
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/{user}"
    auth_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    session["state"] = state
    return redirect(auth_url)

@app.route("/oauth2/callback/<user>")
def oauth2_callback_user(user):
    state = session.get("state")
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/{user}"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials_for(user, creds)
    return f"✅ {user} 연결 완료! <a href='/'>홈</a>"

# --- 캘린더 확인(사용자별) ---
@app.route("/whoami/<user>")
def whoami(user):
    creds = load_credentials_for(user)
    if not creds:
        return f"{user} 토큰 없음. 먼저 /auth/{user} 로 로그인하세요."
    service = build_service(creds)
    info = service.calendars().get(calendarId="primary").execute()
    return f"{user} 캘린더: {info.get('summary')}"

# --- 단일 사용자 이벤트 생성 (me) ---
@app.route("/make_test_event")
def make_test_event():
    creds = load_credentials_for("me")
    if not creds:
        return "먼저 /auth/me 로 로그인하세요."
    service = build_service(creds)

    from datetime import datetime, timedelta, timezone
    KST = timezone(timedelta(hours=9))
    start = datetime.now(KST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    end   = start + timedelta(hours=1)

    event = {
        "summary": "테스트 회의",
        "start": {"dateTime": start.isoformat()},
        "end":   {"dateTime": end.isoformat()},
        # "attendees": [{"email": "본인이_테스트할_다른_이메일@example.com"}],
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return f"✅ 생성! <a href='{created.get('htmlLink')}' target='_blank'>열기</a>"

# --- 엘리스/밥 두 계정에 '각자' 동시 생성 ---
@app.route("/make_test_event_multi")
def make_test_event_multi():
    users = ["alice", "bob"]  # 두 사람의 '개별' 캘린더에 동일 이벤트 생성
    from datetime import datetime, timedelta, timezone
    KST = timezone(timedelta(hours=9))
    start = datetime.now(KST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    end   = start + timedelta(hours=1)

    attendees = []
    if NAME_TO_EMAIL.get("alice"): attendees.append({"email": NAME_TO_EMAIL["alice"]})
    if NAME_TO_EMAIL.get("bob"):   attendees.append({"email": NAME_TO_EMAIL["bob"]})

    results = []
    for user in users:
        creds = load_credentials_for(user)
        if not creds:
            results.append(f"❌ {user}: 먼저 /auth/{user} 로 로그인하세요.")
            continue

        service = build_service(creds)
        event = {
            "summary": "멀티 테스트 회의",
            "start": {"dateTime": start.isoformat()},
            "end":   {"dateTime": end.isoformat()},
            "attendees": attendees,  # 서로를 참석자로도 초대(메일 발송)
        }
        created = service.events().insert(
            calendarId="primary", body=event, sendUpdates="all"
        ).execute()
        results.append(f"✅ {user}: 생성됨 → <a href='{created.get('htmlLink')}' target='_blank'>열기</a>")
    return "<br>".join(results)

# --- 등록된 라우트 확인 ---
@app.route("/routes")
def show_routes():
    return "<br>".join(sorted(rule.rule for rule in app.url_map.iter_rules()))

# ===== 엔트리포인트 =====
if __name__ == "__main__":
    print(">>> FLASK APP:", __name__)
    app.run(port=5000, debug=True)
