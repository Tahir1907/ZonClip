"""Post finished clips to YouTube Shorts, TikTok, and Instagram Reels from ZonClip Studio.

FREE / local-only design:
  * OAuth uses the app's OWN local server as the loopback redirect
    (http://127.0.0.1:<dash_port>/oauth2/<platform>/callback), so NO public website is
    needed for sign-in. The consent page opens in the user's real browser (platforms block
    embedded webviews), then redirects back to ClipForge, which captures the code.
  * Client credentials, tokens, and the schedule queue all live in posting.json under
    LOCAL_ROOT (same place as the API key — local only, never synced, treated like a password).
  * A daemon scheduler uploads queued clips at their chosen time while ClipForge is running.

Reality (see the research write-up that preceded this build): without passing each platform's
public-posting AUDIT (which needs a public website + privacy policy), posting is SEMI-automated:
  * YouTube  — an UNVERIFIED API project force-locks uploads to PRIVATE regardless of the
               privacyStatus we send. So clips upload to your channel and you flip them to
               Public (or schedule) in YouTube Studio. (An audited project honors the privacy
               we pass, making it fully hands-off.)
  * TikTok   — the free path sends clips to your TikTok INBOX (the Creator's Draft endpoint). You open
               the TikTok app, tap the inbox notification, add the caption, and Post (can be public there).
               Direct API publishing needs the audit.

Everything here degrades gracefully: if optional Google/requests libraries aren't installed,
the rest of ClipForge is unaffected and the posting actions surface a friendly "pip install" error.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse

from . import config
from .util import log

# Loopback redirect is plain http://127.0.0.1 — tell oauthlib that's fine (it is, for loopback),
# and relax scope-equality (Google may reorder/extend the granted scope list).
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

STORE_PATH = os.path.join(config.LOCAL_ROOT, "posting.json")
_LOCK = threading.RLock()

YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
             "https://www.googleapis.com/auth/youtube.readonly"]   # readonly => show which channel
YT_TOKEN_URI = "https://oauth2.googleapis.com/token"
YT_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_INBOX_INIT = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TT_STATUS = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TT_USERINFO = "https://open.tiktokapis.com/v2/user/info/"
TT_SCOPES = "user.info.basic,video.upload"
TT_MAX_BYTES = 64 * 1024 * 1024          # files up to this upload in ONE chunk; larger are split
TT_CHUNK = 32 * 1024 * 1024              # per-chunk size for multi-chunk uploads (TikTok allows 5..64MB)


# ----------------------------------------------------------------------------- store
def _load() -> dict:
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            d = {}
    except Exception:  # noqa: BLE001  (missing/corrupt -> fresh)
        d = {}
    d.setdefault("youtube", {})
    d.setdefault("tiktok", {})
    d.setdefault("instagram", {})
    d.setdefault("queue", [])
    d.setdefault("seq", 0)
    d.setdefault("daily_usage", {})
    return d


def _save(d: dict) -> None:
    os.makedirs(config.LOCAL_ROOT, exist_ok=True)
    tmp = STORE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, STORE_PATH)


# ----------------------------------------------------------------------------- Poster
class Poster:
    """Owns credentials, the OAuth handshakes, the uploaders, and the schedule loop."""

    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self._pending_yt: dict = {}        # state -> google flow (in-flight connect)
        self._pending_tt: dict = {}        # state -> pkce code_verifier
        self._pending_ig: set = set()       # one-time OAuth CSRF states
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.on_uploaded = None              # optional callback(item, result) fired after a successful upload

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        # recover anything that was mid-upload when the app last closed -> retry it
        with _LOCK:
            d = _load()
            stuck = [it for it in d["queue"] if it.get("status") == "uploading"]
            for it in stuck:
                it["status"] = "queued"
            if stuck:
                _save(d)
                log(f"[posting] requeued {len(stuck)} interrupted upload(s).")
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clipforge-scheduler")
        self._thread.start()
        log("[posting] scheduler started.")

    def _redirect(self, platform: str) -> str:
        return f"http://127.0.0.1:{self.cfg.dash_port}/oauth2/{platform}/callback"

    def _creds(self, platform: str) -> dict:
        with _LOCK:
            d = _load()
            return dict(d.get(platform) or {})

    # -- public state for the UI -------------------------------------------
    def state(self) -> dict:
        with _LOCK:
            d = _load()
        yt, tt, ig = d["youtube"], d["tiktok"], d["instagram"]
        q = [{k: it.get(k) for k in ("id", "platform", "clip", "title", "when",
                                     "status", "progress", "error", "result")}
             for it in d["queue"][-60:]]
        q.reverse()                         # newest first
        today = time.strftime("%Y-%m-%d")
        usage = (d.get("daily_usage") or {}).get(today) or {}
        return {
            "usage": {"date": today, "youtube_uploads": int(usage.get("youtube", 0)),
                      "tiktok_uploads": int(usage.get("tiktok", 0)),
                      "instagram_uploads": int(usage.get("instagram", 0))},
            "youtube": {"configured": bool(yt.get("client_id") and yt.get("client_secret")),
                        "connected": bool(yt.get("refresh_token")),
                        "channel": yt.get("channel", "")},
            "tiktok": {"configured": bool(tt.get("client_key") and tt.get("client_secret")),
                       "connected": bool(tt.get("access_token")),
                       "handle": tt.get("handle", "")},
            "instagram": {"configured": bool(ig.get("app_id") and ig.get("app_secret")),
                          "connected": bool(ig.get("page_access_token") and ig.get("ig_user_id")),
                          "username": ig.get("username", ""), "page": ig.get("page_name", "")},
            "queue": q,
            "redirect": {"youtube": self._redirect("youtube"), "tiktok": self._redirect("tiktok"),
                         "instagram": self._redirect("instagram")},
        }

    def save_creds(self, platform: str, body: dict) -> None:
        with _LOCK:
            d = _load()
            if platform == "youtube":
                d["youtube"]["client_id"] = (body.get("client_id") or "").strip()
                d["youtube"]["client_secret"] = (body.get("client_secret") or "").strip()
            elif platform == "tiktok":
                d["tiktok"]["client_key"] = (body.get("client_key") or "").strip()
                d["tiktok"]["client_secret"] = (body.get("client_secret") or "").strip()
            elif platform == "instagram":
                d["instagram"]["app_id"] = (body.get("app_id") or "").strip()
                d["instagram"]["app_secret"] = (body.get("app_secret") or "").strip()
            else:
                raise RuntimeError("unknown platform")
            _save(d)

    def disconnect(self, platform: str) -> None:
        """Forget tokens (keep the client id/secret so reconnecting is one click)."""
        keep = (("client_id", "client_secret") if platform == "youtube" else
                ("client_key", "client_secret") if platform == "tiktok" else
                ("app_id", "app_secret") if platform == "instagram" else ())
        if not keep:
            raise RuntimeError("unknown platform")
        with _LOCK:
            d = _load()
            cur = d.get(platform) or {}
            d[platform] = {k: cur[k] for k in keep if k in cur}
            _save(d)

    # =======================================================================
    # YouTube
    # =======================================================================
    def youtube_auth_url(self) -> str:
        cr = self._creds("youtube")
        if not (cr.get("client_id") and cr.get("client_secret")):
            raise RuntimeError("Add your Google client ID + secret first (Connections ▸ YouTube).")
        try:
            from google_auth_oauthlib.flow import Flow
        except ImportError as e:
            raise RuntimeError("Google libraries missing. Run: pip install -r requirements.txt") from e
        redirect = self._redirect("youtube")
        flow = Flow.from_client_config(
            {"web": {"client_id": cr["client_id"], "client_secret": cr["client_secret"],
                     "auth_uri": YT_AUTH_URI, "token_uri": YT_TOKEN_URI,
                     "redirect_uris": [redirect]}},
            scopes=YT_SCOPES, redirect_uri=redirect)
        url, state = flow.authorization_url(access_type="offline", prompt="consent")
        self._pending_yt[state] = flow
        return url

    def youtube_callback(self, code: str, state: str) -> None:
        flow = self._pending_yt.pop(state, None)
        if not flow:
            raise RuntimeError("Sign-in expired or came back out of order — click Connect again.")
        if not code:
            raise RuntimeError("No authorization code returned.")
        flow.fetch_token(code=code)
        creds = flow.credentials
        if not creds.refresh_token:
            raise RuntimeError("Google didn't return a refresh token. Disconnect this app at "
                               "myaccount.google.com/permissions, then Connect again.")
        channel = self._yt_channel_title(creds)
        with _LOCK:
            d = _load()
            d["youtube"].update({
                "refresh_token": creds.refresh_token,
                "token": creds.token,
                "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
                "scopes": list(creds.scopes or YT_SCOPES),
                "channel": channel,
            })
            _save(d)
        log(f"[posting] YouTube connected: {channel or '(channel name unavailable)'}")

    def _yt_channel_title(self, creds) -> str:
        try:
            from googleapiclient.discovery import build
            svc = build("youtube", "v3", credentials=creds, cache_discovery=False)
            r = svc.channels().list(part="snippet", mine=True).execute()
            items = r.get("items", [])
            return items[0]["snippet"]["title"] if items else ""
        except Exception as e:  # noqa: BLE001
            log(f"[posting] yt channel lookup failed: {e}")
            return ""

    def _yt_credentials(self):
        from google.oauth2.credentials import Credentials
        with _LOCK:
            yt = dict(_load()["youtube"])
        if not yt.get("refresh_token"):
            raise RuntimeError("YouTube isn't connected yet.")
        return Credentials(
            token=yt.get("token"), refresh_token=yt["refresh_token"],
            token_uri=YT_TOKEN_URI, client_id=yt.get("client_id"),
            client_secret=yt.get("client_secret"), scopes=yt.get("scopes") or YT_SCOPES)

    def _yt_persist_token(self, creds) -> None:
        with _LOCK:
            d = _load()
            d["youtube"]["token"] = creds.token
            try:
                d["youtube"]["expiry"] = creds.expiry.isoformat() if creds.expiry else None
            except Exception:  # noqa: BLE001
                pass
            _save(d)

    def youtube_upload(self, item: dict, progress_cb=None) -> dict:
        try:
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as e:
            raise RuntimeError("Google libraries missing. Run: pip install -r requirements.txt") from e
        creds = self._yt_credentials()
        svc = build("youtube", "v3", credentials=creds, cache_discovery=False)

        tags = [str(t).lstrip("#") for t in (item.get("hashtags") or []) if str(t).strip()]
        desc = (item.get("description") or "").strip()
        if tags:
            desc = (desc + "\n\n" + " ".join("#" + t for t in tags)).strip()
        if "#shorts" not in desc.lower():               # nudge YouTube to classify it as a Short
            desc = (desc + "\n\n#Shorts").strip()
        body = {
            "snippet": {"title": (item.get("title") or "Clip").strip()[:100],
                        "description": desc[:4900], "tags": tags[:15], "categoryId": "20"},  # 20 = Gaming
            "status": {"privacyStatus": item.get("privacy") or "private",
                       "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(item["mp4"], chunksize=4 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        req = svc.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            status, resp = req.next_chunk()
            if status and progress_cb:
                progress_cb(status.progress())
        vid = resp["id"]
        actual_privacy = (resp.get("status") or {}).get("privacyStatus", body["status"]["privacyStatus"])

        cover = item.get("cover")
        if cover and os.path.exists(cover):
            try:
                svc.thumbnails().set(videoId=vid,
                                     media_body=MediaFileUpload(cover, mimetype="image/jpeg")).execute()
            except Exception as e:  # noqa: BLE001  (custom thumbnails need a verified channel)
                log(f"[posting] yt thumbnail skipped: {e}")
        self._yt_persist_token(creds)
        return {"video_id": vid, "url": f"https://youtu.be/{vid}", "privacy": actual_privacy}

    # =======================================================================
    # TikTok
    # =======================================================================
    def tiktok_auth_url(self) -> str:
        cr = self._creds("tiktok")
        if not (cr.get("client_key") and cr.get("client_secret")):
            raise RuntimeError("Add your TikTok client key + secret first (Connections ▸ TikTok).")
        verifier = secrets.token_hex(48)                # 96 chars, in PKCE's 43..128 range
        # TikTok deviates from RFC 7636: code_challenge is the HEX digest of SHA256 (NOT base64url),
        # while code_challenge_method stays "S256". Sending base64url -> token error
        # "Code verifier or code challenge is invalid".
        challenge = hashlib.sha256(verifier.encode()).hexdigest()
        state = secrets.token_urlsafe(16)
        self._pending_tt[state] = verifier
        q = {"client_key": cr["client_key"], "scope": TT_SCOPES, "response_type": "code",
             "redirect_uri": self._redirect("tiktok"), "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return TT_AUTH + "?" + urllib.parse.urlencode(q)

    def tiktok_callback(self, code: str, state: str) -> None:
        import requests
        verifier = self._pending_tt.pop(state, None)
        if not verifier:
            raise RuntimeError("Sign-in expired or came back out of order — click Connect again.")
        if not code:
            raise RuntimeError("No authorization code returned.")
        cr = self._creds("tiktok")
        r = requests.post(TT_TOKEN, timeout=30,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"client_key": cr["client_key"], "client_secret": cr["client_secret"],
                                "code": code, "grant_type": "authorization_code",
                                "redirect_uri": self._redirect("tiktok"), "code_verifier": verifier})
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"TikTok token error: {j.get('error_description') or j}")
        now = int(time.time())
        with _LOCK:
            d = _load()
            d["tiktok"].update({
                "access_token": j["access_token"], "refresh_token": j.get("refresh_token"),
                "open_id": j.get("open_id"), "scope": j.get("scope"),
                "expires_at": now + int(j.get("expires_in", 0) or 0),
                "refresh_expires_at": now + int(j.get("refresh_expires_in", 0) or 0),
            })
            _save(d)
        self._tiktok_fetch_handle()
        log("[posting] TikTok connected.")

    def _tiktok_token(self) -> str:
        import requests
        with _LOCK:
            tt = dict(_load()["tiktok"])
        if not tt.get("access_token"):
            raise RuntimeError("TikTok isn't connected yet.")
        if int(tt.get("expires_at", 0)) - 60 > int(time.time()):
            return tt["access_token"]
        cr = self._creds("tiktok")
        r = requests.post(TT_TOKEN, timeout=30,
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          data={"client_key": cr["client_key"], "client_secret": cr["client_secret"],
                                "grant_type": "refresh_token", "refresh_token": tt.get("refresh_token")})
        j = r.json()
        if "access_token" not in j:
            raise RuntimeError(f"TikTok session expired and refresh failed — reconnect TikTok. ({j.get('error_description') or j})")
        now = int(time.time())
        with _LOCK:
            d = _load()
            d["tiktok"].update({
                "access_token": j["access_token"],
                "refresh_token": j.get("refresh_token", tt.get("refresh_token")),
                "expires_at": now + int(j.get("expires_in", 0) or 0),
            })
            _save(d)
        return j["access_token"]

    def _tiktok_fetch_handle(self) -> None:
        try:
            import requests
            tok = self._tiktok_token()
            r = requests.get(TT_USERINFO, params={"fields": "display_name"}, timeout=20,
                             headers={"Authorization": f"Bearer {tok}"})
            user = ((r.json() or {}).get("data") or {}).get("user") or {}
            handle = user.get("display_name") or ""
            if handle:
                with _LOCK:
                    d = _load()
                    d["tiktok"]["handle"] = handle
                    _save(d)
        except Exception as e:  # noqa: BLE001
            log(f"[posting] tiktok handle lookup failed: {e}")

    def tiktok_upload(self, item: dict, progress_cb=None) -> dict:
        import requests
        tok = self._tiktok_token()
        path = item["mp4"]
        size = os.path.getsize(path)
        # TikTok FILE_UPLOAD chunking: <=64MB goes in one chunk; larger files split into equal chunks
        # (each 5..64MB) with the final chunk absorbing the remainder. total = floor(size/chunk).
        if size <= TT_MAX_BYTES:
            chunk_size, total = size, 1
        else:
            chunk_size = TT_CHUNK
            total = size // chunk_size
        init = requests.post(TT_INBOX_INIT, timeout=60,
                             headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                             json={"source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                                   "chunk_size": chunk_size, "total_chunk_count": total}})
        j = init.json()
        err = (j.get("error") or {})
        if err and err.get("code") not in (None, "ok"):
            raise RuntimeError(f"TikTok init error: {err.get('message') or err}")
        data = j.get("data") or {}
        publish_id, upload_url = data.get("publish_id"), data.get("upload_url")
        if not upload_url:
            raise RuntimeError(f"TikTok init returned no upload URL: {j}")
        with open(path, "rb") as fh:
            for i in range(total):
                start = i * chunk_size
                end = size - 1 if i == total - 1 else start + chunk_size - 1   # last chunk -> EOF
                fh.seek(start)
                blob = fh.read(end - start + 1)
                put = requests.put(upload_url, data=blob, timeout=900,
                                   headers={"Content-Type": "video/mp4", "Content-Length": str(len(blob)),
                                            "Content-Range": f"bytes {start}-{end}/{size}"})
                if put.status_code not in (200, 201, 204, 206):   # 206 = chunk accepted; 201 = final chunk done
                    raise RuntimeError(f"TikTok upload failed ({put.status_code}): {put.text[:200]}")
                if progress_cb:
                    progress_cb((i + 1) / total)
        return {"publish_id": publish_id, "where": "TikTok inbox"}

    # =======================================================================
    # Instagram Reels (Facebook Login for Business; resumable upload)
    # =======================================================================
    IG_VERSION = "v27.0"
    IG_GRAPH = f"https://graph.facebook.com/{IG_VERSION}"
    IG_AUTH = f"https://www.facebook.com/{IG_VERSION}/dialog/oauth"

    def instagram_auth_url(self) -> str:
        cr = self._creds("instagram")
        if not (cr.get("app_id") and cr.get("app_secret")):
            raise RuntimeError("Add your Meta App ID + App Secret first (Connections ▸ Instagram).")
        state = secrets.token_urlsafe(32)
        self._pending_ig.add(state)
        q = {
            "client_id": cr["app_id"], "redirect_uri": self._redirect("instagram"),
            "response_type": "code",
            "scope": "instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement",
            "state": state,
        }
        return self.IG_AUTH + "?" + urllib.parse.urlencode(q)

    def instagram_callback(self, code: str, state: str) -> None:
        import requests
        if not state or state not in self._pending_ig:
            raise RuntimeError("Instagram sign-in expired or state did not match. Connect again.")
        self._pending_ig.remove(state)
        if not code:
            raise RuntimeError("No Instagram authorization code returned.")
        cr = self._creds("instagram")
        redirect = self._redirect("instagram")
        token_response = requests.get(
            f"{self.IG_GRAPH}/oauth/access_token", timeout=30,
            params={"client_id": cr["app_id"], "client_secret": cr["app_secret"],
                    "redirect_uri": redirect, "code": code})
        token_data = token_response.json()
        if token_response.status_code >= 400 or not token_data.get("access_token"):
            raise RuntimeError(f"Meta token exchange failed: {token_data.get('error', token_data)}")
        short_token = token_data["access_token"]
        long_response = requests.get(
            f"{self.IG_GRAPH}/oauth/access_token", timeout=30,
            params={"grant_type": "fb_exchange_token", "client_id": cr["app_id"],
                    "client_secret": cr["app_secret"], "fb_exchange_token": short_token})
        long_data = long_response.json()
        if long_response.status_code >= 400 or not long_data.get("access_token"):
            raise RuntimeError(f"Meta long-lived token exchange failed: {long_data.get('error', long_data)}")
        user_token = long_data["access_token"]
        pages_response = requests.get(
            f"{self.IG_GRAPH}/me/accounts", timeout=30,
            params={"fields": "id,name,access_token,instagram_business_account{id,username}",
                    "access_token": user_token})
        pages_data = pages_response.json()
        if pages_response.status_code >= 400:
            raise RuntimeError(f"Meta could not list Pages: {pages_data.get('error', pages_data)}")
        linked = [page for page in pages_data.get("data", [])
                  if (page.get("instagram_business_account") or {}).get("id") and page.get("access_token")]
        if not linked:
            raise RuntimeError(
                "No linked Instagram professional account was found. Connect an Instagram Business/Creator "
                "account to a Facebook Page, then grant the requested Meta permissions and connect again.")
        if len(linked) > 1:
            raise RuntimeError(
                "More than one Page has a linked Instagram professional account. Disconnect or remove "
                "unused Page access so only the intended account is granted to this app, then reconnect.")
        page = linked[0]
        account = page["instagram_business_account"]
        with _LOCK:
            d = _load()
            d["instagram"].update({
                "user_access_token": user_token,
                "page_access_token": page["access_token"],
                "ig_user_id": account["id"],
                "username": account.get("username", ""),
                "page_id": page["id"], "page_name": page.get("name", ""),
                "expires_at": int(time.time()) + int(long_data.get("expires_in", token_data.get("expires_in", 0)) or 0),
            })
            _save(d)
        log(f"[posting] Instagram connected: @{account.get('username') or account['id']}")

    def _instagram_credentials(self) -> dict:
        with _LOCK:
            ig = dict(_load()["instagram"])
        if not ig.get("page_access_token") or not ig.get("ig_user_id"):
            raise RuntimeError("Instagram isn't connected yet.")
        if ig.get("expires_at") and int(ig["expires_at"]) < int(time.time()) + 60:
            raise RuntimeError("Instagram access token is expired or near expiry. Disconnect and reconnect Instagram.")
        return ig

    def instagram_upload(self, item: dict, progress_cb=None) -> dict:
        import requests
        path = item.get("mp4") or ""
        if not os.path.isfile(path):
            raise RuntimeError("The Instagram Reel file is missing.")
        size = os.path.getsize(path)
        if size <= 0 or size > 1024 * 1024 * 1024:
            raise RuntimeError("Instagram Reels must be a non-empty file no larger than 1 GB.")
        try:
            duration = __import__("pipeline.util", fromlist=["probe_duration"]).probe_duration(path)
        except Exception:
            duration = 0
        if duration and not (3 <= duration <= 900):
            raise RuntimeError("Instagram Reels must be between 3 seconds and 15 minutes.")
        ig = self._instagram_credentials()
        token, ig_id = ig["page_access_token"], ig["ig_user_id"]
        caption = (item.get("caption") or "").strip()
        hashtags = [str(x).lstrip("#") for x in (item.get("hashtags") or []) if str(x).strip()]
        if hashtags:
            caption = (caption + "\n\n" + " ".join("#" + x for x in hashtags)).strip()
        caption = caption[:2200]

        # Ask Meta for its live quota state before starting a new publish operation.
        quota = requests.get(f"{self.IG_GRAPH}/{ig_id}/content_publishing_limit", timeout=30,
                             params={"access_token": token})
        quota_data = quota.json()
        if quota.status_code >= 400:
            raise RuntimeError(f"Instagram quota check failed: {quota_data.get('error', quota_data)}")
        quota_rows = quota_data.get("data") or []
        if quota_rows:
            row = quota_rows[0]
            usage = row.get("quota_usage")
            config_data = row.get("config") or {}
            total = config_data.get("quota_total")
            if usage is not None and total is not None and int(usage) >= int(total):
                raise RuntimeError("Instagram's current publishing quota is full. Wait for its rolling window to reset.")

        create = requests.post(
            f"{self.IG_GRAPH}/{ig_id}/media", timeout=60,
            data={"media_type": "REELS", "upload_type": "resumable",
                  "caption": caption, "share_to_feed": "true", "access_token": token})
        created = create.json()
        if create.status_code >= 400 or not created.get("id") or not created.get("uri"):
            raise RuntimeError(f"Instagram Reel container creation failed: {created.get('error', created)}")
        container_id, upload_uri = created["id"], created["uri"]

        with open(path, "rb") as stream:
            uploaded = requests.post(
                upload_uri, data=stream, timeout=900,
                headers={"Authorization": f"OAuth {token}", "offset": "0",
                         "file_size": str(size), "Content-Type": "application/octet-stream"})
        try:
            upload_data = uploaded.json() if uploaded.content else {}
        except ValueError:
            upload_data = {"response": (uploaded.text or "")[:300]}
        if uploaded.status_code >= 400 or upload_data.get("success") is False:
            raise RuntimeError(f"Instagram video upload failed: {upload_data}")
        if progress_cb:
            progress_cb(0.75)

        deadline = time.time() + 900
        status = "IN_PROGRESS"
        while time.time() < deadline:
            poll = requests.get(f"{self.IG_GRAPH}/{container_id}", timeout=30,
                                params={"fields": "status_code,status", "access_token": token})
            poll_data = poll.json()
            if poll.status_code >= 400:
                raise RuntimeError(f"Instagram processing check failed: {poll_data.get('error', poll_data)}")
            status = poll_data.get("status_code") or poll_data.get("status") or "IN_PROGRESS"
            if status == "FINISHED":
                break
            if status in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram could not process this Reel (container status: {status}).")
            time.sleep(5)
        if status != "FINISHED":
            raise RuntimeError("Instagram is still processing this Reel after 15 minutes. It was not published; retry later.")
        if progress_cb:
            progress_cb(0.9)
        publish = requests.post(
            f"{self.IG_GRAPH}/{ig_id}/media_publish", timeout=60,
            data={"creation_id": container_id, "access_token": token})
        published = publish.json()
        if publish.status_code >= 400 or not published.get("id"):
            raise RuntimeError(f"Instagram publish failed: {published.get('error', published)}")
        media_id = published["id"]
        details = requests.get(f"{self.IG_GRAPH}/{media_id}", timeout=30,
                               params={"fields": "permalink", "access_token": token})
        detail_data = details.json()
        permalink = detail_data.get("permalink") if details.status_code < 400 else None
        if progress_cb:
            progress_cb(1.0)
        return {"media_id": media_id, "url": permalink or "https://www.instagram.com/", "where": "Instagram Reels"}

    # =======================================================================
    # Queue + scheduler
    # =======================================================================
    def enqueue(self, items: list) -> list:
        """Add fully-resolved upload items (self-contained: abs mp4 path + metadata captured now,
        so a scheduled post doesn't depend on which project is open when it fires)."""
        out = []
        now = int(time.time())
        with _LOCK:
            d = _load()
            for it in items:
                d["seq"] += 1
                jid = f"up{d['seq']}"
                rec = {"id": jid, "status": "queued", "progress": 0, "error": "", "result": None,
                       "created": now, "updated": now, **it}
                d["queue"].append(rec)
                out.append(jid)
            # keep the journal from growing forever: drop the oldest finished items past 100
            if len(d["queue"]) > 100:
                done = [x for x in d["queue"] if x.get("status") in ("uploaded", "error")]
                drop = set(x["id"] for x in done[:len(d["queue"]) - 100])
                d["queue"] = [x for x in d["queue"] if x["id"] not in drop]
            _save(d)
        self._wake.set()
        return out

    def cancel(self, jid: str) -> bool:
        """Remove a queued/finished item (never one mid-upload)."""
        with _LOCK:
            d = _load()
            before = len(d["queue"])
            d["queue"] = [x for x in d["queue"]
                          if not (x["id"] == jid and x.get("status") in ("queued", "error", "uploaded"))]
            changed = len(d["queue"]) < before
            if changed:
                _save(d)
        return changed

    def _set(self, jid: str, **kw) -> None:
        with _LOCK:
            d = _load()
            for it in d["queue"]:
                if it["id"] == jid:
                    if kw.get("status") == "uploaded" and it.get("status") != "uploaded":
                        day = time.strftime("%Y-%m-%d")
                        usage = d.setdefault("daily_usage", {}).setdefault(
                            day, {"youtube": 0, "tiktok": 0, "instagram": 0})
                        plat = it.get("platform")
                        if plat in ("youtube", "tiktok", "instagram"):
                            usage[plat] = int(usage.get(plat, 0)) + 1
                        # Keep a month of local counters; these are activity counts, not provider quota units.
                        for old_day in sorted(d["daily_usage"])[:-31]:
                            d["daily_usage"].pop(old_day, None)
                    it.update(kw)
                    it["updated"] = int(time.time())
                    break
            _save(d)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:  # noqa: BLE001
                log(f"[posting] scheduler tick error: {e}")
            self._wake.wait(20)
            self._wake.clear()

    def _tick(self) -> None:
        now = time.time()
        with _LOCK:
            d = _load()
            due = [it for it in d["queue"]
                   if it.get("status") == "queued" and (it.get("when") or 0) <= now]
            ids = [it["id"] for it in due]
            for it in due:
                it["status"] = "uploading"
                it["updated"] = int(now)
            if due:
                _save(d)
        for jid in ids:                       # serialize: one upload at a time
            self._run_one(jid)

    def _run_one(self, jid: str) -> None:
        with _LOCK:
            d = _load()
            it = next((x for x in d["queue"] if x["id"] == jid), None)
            it = dict(it) if it else None
        if not it:
            return
        plat = it.get("platform")

        def pcb(frac):
            self._set(jid, progress=max(0, min(100, int(frac * 100))))

        try:
            if not os.path.exists(it.get("mp4") or ""):
                raise RuntimeError("the clip file is gone — re-download it, then post again.")
            if plat == "youtube":
                res = self.youtube_upload(it, pcb)
            elif plat == "tiktok":
                res = self.tiktok_upload(it, pcb)
            elif plat == "instagram":
                res = self.instagram_upload(it, pcb)
            else:
                raise RuntimeError(f"Unsupported posting platform: {plat}")
            self._set(jid, status="uploaded", result=res, error="", progress=100)
            log(f"[posting] {plat} upload done for {it.get('clip')} -> {res}")
            if self.on_uploaded:
                try:
                    self.on_uploaded(it, res)            # let the server stamp a 'posted' marker on the clip
                except Exception as e:  # noqa: BLE001
                    log(f"[posting] on_uploaded hook error: {e}")
        except Exception as e:  # noqa: BLE001
            message = str(e)
            lowered = message.lower()
            if plat == "youtube" and any(term in lowered for term in
                                         ("quotaexceeded", "dailylimitexceeded", "quota exceeded")):
                message = ("YouTube API quota was reached. Check the YouTube Data API quota in "
                           "Google Cloud Console, then retry this item when the limit resets. " + message[:400])
            elif any(term in lowered for term in ("429", "613", "rate limit", "rate_limit", "too many requests")):
                message = (f"{plat.title()} rate limit was reached. Wait for the platform limit to reset, "
                           "then retry this item. " + message[:400])
            self._set(jid, status="error", error=message)
            log(f"[posting] {plat} upload {jid} failed: {message}")


# ----------------------------------------------------------------------------- module singleton
POSTER: Poster | None = None


def init(cfg: config.Config) -> Poster:
    """Get (creating + starting the scheduler on first call) the process-wide Poster."""
    global POSTER
    if POSTER is None:
        POSTER = Poster(cfg)
        POSTER.start()
    return POSTER
