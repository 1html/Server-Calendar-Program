# app_oauth.py
# -----------------------------------------------------------
# Google Calendar + OAuth2 + Multi-user + GPT 자연어 이벤트 생성
# -----------------------------------------------------------
import os, json
from datetime import datetime, timedelta, timezone

from flask import Flask, request, redirect, session
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from oauthlib.oauth2.rfc6749.errors import MismatchingStateError

# ─────────────────────────────
# Flask & 세션/프록시 설정
# ─────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "dev-secret")

# 크로스 사이트 리다이렉트에서도 세션 쿠키 전달(HTTPS 필수)
app.config.update(
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_SECURE=True,
)

# 프록시 뒤에서 스킴/호스트 보정(Render 등)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# 배포 도메인(https), 로컬 개발 시 http://localhost:5000
BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
if BASE_URL.startswith("http://"):
    # 로컬 HTTP에서 OAuth 허용 (배포에서는 불필요)
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# ─────────────────────────────
# Google OAuth / Calendar 설정
# ─────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# GOOGLE_CLIENT_CONFIG_JSON 우선, 없으면 client_secret.json 파일 사용
if "GOOGLE_CLIENT_CONFIG_JSON" in os.environ:
    GOOGLE_CLIENT_CONFIG = json.loads(os.environ["GOOGLE_CLIENT_CONFIG_JSON"])
else:
    with open("client_secret.json", "r", encoding="utf-8") as f:
        GOOGLE_CLIENT_CONFIG = json.load(f)

TOKENS_DIR = "tokens"
os.makedirs(TOKENS_DIR, exist_ok=True)

def token_path(user: str) -> str:
    return os.path.join(TOKENS_DIR, f"{user}.json")

def save_credentials_for(user: str, creds: Credentials):
    with open(token_path(user), "w", encoding="utf-8") as f:
        f.write(creds.to_json())

def load_credentials_for(user: str):
    p = token_path(user)
    if not os.path.exists(p):
        return None
    return Credentials.from_authorized_user_file(p, SCOPES)

def build_service(creds: Credentials):
    return build("calendar", "v3", credentials=creds)

# 이름→이메일 매핑(테스트용: 실제 주소로 바꿔쓰기)
NAME_TO_EMAIL = {
    "alice": "alice_실제이메일@example.com",
    "bob":   "bob_실제이메일@example.com",
    "엘리스": "alice_실제이메일@example.com",
    "밥":     "bob_실제이메일@example.com",
}

def normalize_attendees(items):
    """이름이면 매핑, 이메일이면 그대로"""
    out = []
    for x in items or []:
        x = x.strip()
        if "@" in x:
            out.append({"email": x})
        else:
            out.append({"email": NAME_TO_EMAIL.get(x.lower(), x)})
    return out

# KST(Asia/Seoul)
KST = timezone(timedelta(hours=9))

# ─────────────────────────────
# OpenAI (자연어 → JSON)
# ─────────────────────────────
import openai
openai.api_key = os.environ.get("OPENAI_API_KEY")

def nlp_to_event_json(user_text: str) -> dict:
    """자연어 문장을 이벤트 JSON으로 변환(오늘 날짜를 명시적으로 제공)"""
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    prompt = f"""
오늘은 {today_str} 입니다. 다음 한국어 문장을 분석해서 캘린더 이벤트 JSON을 만들어줘.
반드시 JSON 하나만 출력하고, 추가 텍스트를 쓰지 마.
- summary: 문자열(없으면 '회의')
- start: ISO 8601 datetime with timezone (예: 2025-09-02T15:00:00+09:00)
- end:   ISO 8601 datetime with timezone
- attendees: 이메일 문자열 배열(없으면 빈 배열)
문장: "{user_text}"
"""
    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = resp.choices[0].message["content"]
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # 모델이 포맷을 깨면 보수적으로 빈 이벤트 반환(상위에서 처리)
        parsed = {}
    return parsed

# ─────────────────────────────
# 라우트: 홈/헬스체크/디버그
# ─────────────────────────────
@app.route("/")
def home():
    return (
        "<h2>Calendar Project</h2>"
        "<ul>"
        "<li><a href='/auth/me'>/auth/me (단일 사용자)</a></li>"
        "<li><a href='/auth/alice'>/auth/alice</a></li>"
        "<li><a href='/auth/bob'>/auth/bob</a></li>"
        "<li><a href='/whoami/me'>/whoami/me</a></li>"
        "<li><a href='/whoami/alice'>/whoami/alice</a></li>"
        "<li><a href='/whoami/bob'>/whoami/bob</a></li>"
        "<li><a href='/make_test_event'>/make_test_event</a></li>"
        "<li><a href='/make_test_event_multi'>/make_test_event_multi</a></li>"
        "<li><a href='/nlp_form/alice'>/nlp_form/alice</a> (자연어 입력 폼)</li>"
        "<li><a href='/routes'>/routes</a></li>"
        "</ul>"
        f"<p>BASE_URL: {BASE_URL}</p>"
    )

@app.route("/routes")
def show_routes():
    return "<br>".join(sorted(rule.rule for rule in app.url_map.iter_rules()))

@app.route("/debug/env")
def debug_env():
    cfg = os.environ.get("GOOGLE_CLIENT_CONFIG_JSON", "")
    preview = (cfg[:150] + "...") if cfg else "(missing)"
    return (
        f"BASE_URL = {BASE_URL}<br>"
        f"APP_SECRET set = {bool(os.environ.get('APP_SECRET'))}<br>"
        f"GOOGLE_CLIENT_CONFIG_JSON = {'set' if cfg else 'MISSING'}<br>"
        f"OPENAI_API_KEY set = {bool(os.environ.get('OPENAI_API_KEY'))}<br>"
        f"preview = {preview}"
    )

@app.route("/debug/redirect/<user>")
def debug_redirect(user):
    return f"redirect_uri = {BASE_URL}/oauth2/callback/{user}"

# ─────────────────────────────
# OAuth: 단일(me) + 멀티 사용자
# ─────────────────────────────
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
    # 사용자가 콜백 URL을 직접 치면 state가 없음 → /auth로 유도
    if not request.args.get("state"):
        return redirect("/auth/me")
    state = session.get("state")
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/me"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials_for("me", creds)
    service = build_service(creds)
    info = service.calendars().get(calendarId="primary").execute()
    return f"✅ me 연결 완료!<br>캘린더: {info.get('summary')}<br><a href='/'>홈</a>"

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
    if not request.args.get("state"):
        return redirect(f"/auth/{user}")
    state = session.get("state")
    flow = Flow.from_client_config(GOOGLE_CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = f"{BASE_URL}/oauth2/callback/{user}"
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    save_credentials_for(user, creds)
    return f"✅ {user} 연결 완료! <a href='/'>홈</a>"

# 에러 핸들러: state 불일치 시 부드럽게 복구
@app.errorhandler(MismatchingStateError)
def handle_state_error(e):
    return redirect("/"), 302

# ─────────────────────────────
# 캘린더: 확인 & 테스트 이벤트 생성
# ─────────────────────────────
@app.route("/whoami/<user>")
def whoami(user):
    creds = load_credentials_for(user)
    if not creds:
        return f"{user} 토큰 없음. 먼저 /auth/{user} 로 로그인하세요."
    service = build_service(creds)
    info = service.calendars().get(calendarId="primary").execute()
    return f"{user} 캘린더: {info.get('summary')}"

@app.route("/make_test_event")
def make_test_event():
    creds = load_credentials_for("me")
    if not creds:
        return "먼저 /auth/me 로 로그인하세요."
    service = build_service(creds)

    start = datetime.now(KST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    end   = start + timedelta(hours=1)
    event = {
        "summary": "테스트 회의",
        "start": {"dateTime": start.isoformat()},
        "end":   {"dateTime": end.isoformat()},
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return f"✅ 생성! <a href='{created.get('htmlLink')}' target='_blank'>열기</a>"

@app.route("/make_test_event_multi")
def make_test_event_multi():
    users = ["alice", "bob"]  # 두 사람 각자 캘린더에 동일 이벤트 생성
    start = datetime.now(KST).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2)
    end   = start + timedelta(hours=1)
    attendees = normalize_attendees(["alice", "bob"])

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
            "attendees": attendees,  # 서로 초대
        }
        created = service.events().insert(
            calendarId="primary", body=event, sendUpdates="all"
        ).execute()
        results.append(f"✅ {user}: 생성됨 → <a href='{created.get('htmlLink')}' target='_blank'>열기</a>")
    return "<br>".join(results)

# ─────────────────────────────
# 자연어 입력 폼 & 처리 (GPT → 캘린더)
# ─────────────────────────────
@app.route("/nlp_form/<user>")
def nlp_form(user):
    return f"""
    <h3>NLP Event Creator for {user}</h3>
    <form method="POST" action="/nlp_event/{user}">
      <input type="text" name="text" placeholder="예: 오늘 오후 1시~3시 엘리스랑 회의" style="width:360px">
      <button type="submit">등록</button>
    </form>
    """

@app.route("/nlp_event/<user>", methods=["POST"])
def nlp_event(user):
    creds = load_credentials_for(user)
    if not creds:
        return f"{user} 먼저 /auth/{user} 로 로그인하세요."
    text = request.form.get("text")
    if not text:
        return "❌ text 파라미터 필요"

    parsed = nlp_to_event_json(text)
    # 파싱 실패 시 간단한 가드
    start_iso = parsed.get("start")
    end_iso = parsed.get("end")
    summary = parsed.get("summary") or "회의"
    attendees_raw = parsed.get("attendees", [])

    event = {
        "summary": summary,
        "start": {"dateTime": start_iso},
        "end":   {"dateTime": end_iso},
        "attendees": normalize_attendees(attendees_raw),
    }

    # 필수값 없으면 안내
    if not start_iso or not end_iso:
        return "❌ 시간 파싱에 실패했어요. 예: '오늘 오후 1시~3시 회의' 처럼 다시 입력해 주세요."

    service = build_service(creds)
    created = service.events().insert(
        calendarId="primary", body=event, sendUpdates="all"
    ).execute()
    return f"✅ 이벤트 생성 완료! <a href='{created.get('htmlLink')}' target='_blank'>구글 캘린더 확인</a>"

# ─────────────────────────────
# 엔트리포인트
# ─────────────────────────────
if __name__ == "__main__":
    print(">>> FLASK APP:", __name__)
    app.run(port=5000, debug=True)
