
from __future__ import annotations

import base64
from io import BytesIO
import json
import sys
import time
from dataclasses import dataclass
from typing import Any

import requests

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:3000"
TIMEOUT = 20


@dataclass
class Case:
    name: str
    ok: bool
    detail: str = ""


class T:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.cases: list[Case] = []
        self.created_jobs: list[str] = []
        self.created_pages: list[str] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.cases.append(Case(name, ok, detail[:300]))

    def req(self, method: str, path: str, **kw: Any) -> requests.Response:
        return self.s.request(method, BASE + path, timeout=kw.pop("timeout", TIMEOUT), **kw)

    def expect(self, name: str, method: str, path: str, status: int | tuple[int, ...] = 200, **kw: Any) -> Any:
        try:
            r = self.req(method, path, **kw)
            statuses = status if isinstance(status, tuple) else (status,)
            ok = r.status_code in statuses
            detail = f"HTTP {r.status_code}"
            if not ok:
                detail += " " + r.text[:220]
            self.add(name, ok, detail)
            if r.headers.get("content-type", "").startswith("application/json"):
                return r.json()
            return r.text
        except Exception as exc:
            self.add(name, False, str(exc))
            return None

    def run(self) -> None:
        # 1-6 base/static/API
        self.expect("01 home HTML", "GET", "/")
        self.expect("02 favicon", "GET", "/favicon.ico")
        appjs = self.expect("03 static app.js v2869", "GET", "/static/app.js?v=2869") or ""
        self.add("04 UI has grouped Flow settings", "settings-section" in appjs and "scriptAiModel" in appjs and "\\u0110\\u00e3 click" in appjs, "settings-section + scriptAiModel + click feedback")
        self.expect("05 health", "GET", "/api/health")
        status = self.expect("06 status", "GET", "/api/status")

        # 7-11 templates/settings
        templates = self.expect("07 job templates", "GET", "/api/job-templates") or []
        ids = {str(x.get("id")) for x in templates if isinstance(x, dict)}
        self.add("08 template 1/2/3/4 present", {"1", "2", "3", "4"}.issubset(ids), str(ids))
        flow = self.expect("09 flow settings", "GET", "/api/flow") or {}
        settings = flow.get("settings") or {}
        self.add("10 global flow 9/4 + AI model", settings.get("imageConcurrency") == 9 and settings.get("videoConcurrency") == 4 and settings.get("scriptAiModel") == "ag/gemini-3.1-pro-high", json.dumps(settings, ensure_ascii=False)[:220])
        patch = self.expect("11 flow patch no-op", "PATCH", "/api/flow/settings", json={"scriptAiModel": settings.get("scriptAiModel", "ag/gemini-3.1-pro-high"), "scriptFallbackModel": settings.get("scriptFallbackModel", "cx/gpt-5.5")}) or {}
        self.add("12 flow patch keeps models", (patch.get("scriptAiModel") or "") == "ag/gemini-3.1-pro-high", json.dumps(patch, ensure_ascii=False)[:220])

        # 13-19 job CRUD + clone + schedule
        jobs_before = self.expect("13 jobs list", "GET", "/api/jobs") or []
        create = self.expect("14 create temp job 1", "POST", "/api/jobs", json={"template_id": "1", "name": "TEST_AUTO_DELETE_JOB1", "config": {"model": ""}, "page_ids": []}) or {}
        job = create.get("job") or {}
        jid = job.get("id")
        if jid:
            self.created_jobs.append(jid)
        self.add("15 created job id", bool(jid), str(jid))
        if jid:
            self.expect("16 get temp job", "GET", f"/api/jobs/{jid}")
            self.expect("17 patch temp job", "PATCH", f"/api/jobs/{jid}", json={"name": "TEST_AUTO_DELETE_JOB1_PATCHED", "config": {"model": ""}, "schedule": {"enabled": False, "mode": "manual"}, "page_ids": []})
            clone = self.expect("18 clone temp job", "POST", f"/api/jobs/{jid}/clone", json={"name": "TEST_AUTO_DELETE_CLONE"}) or {}
            cj = (clone.get("job") or {}).get("id")
            if cj:
                self.created_jobs.append(cj)
            self.add("19 cloned job id", bool(cj), str(cj))

        # 20-23 job2 multi-select + asset upload/get
        create2 = self.expect("20 create temp job 2", "POST", "/api/jobs", json={"template_id": "2", "name": "TEST_AUTO_DELETE_JOB2", "config": {"mode": ["IMAGE_BEAT", "IMAGE_TO_VIDEO"], "ai_model": ""}, "page_ids": []}) or {}
        j2 = (create2.get("job") or {}).get("id")
        if j2:
            self.created_jobs.append(j2)
            self.expect("21 get temp job2", "GET", f"/api/jobs/{j2}")
            try:
                from PIL import Image
                buf = BytesIO()
                Image.new("RGB", (2, 2), (255, 255, 255)).save(buf, format="PNG")
                png = buf.getvalue()
            except Exception:
                png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR42mP8z8AABQMBgA0XxXcAAAAASUVORK5CYII=")
            r = self.req("POST", f"/api/jobs/{j2}/assets/persona_path", files={"file": ("persona.png", png, "image/png")})
            self.add("22 upload persona asset", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
            self.expect("23 get persona asset", "GET", f"/api/jobs/{j2}/assets/persona_path")

        # 24 job4 keyword-auto CRUD
        create4 = self.expect("24 create temp job 4 keyword", "POST", "/api/jobs", json={"template_id": "4", "name": "TEST_AUTO_DELETE_JOB4", "config": {"keyword": "\u0111\u1ed3 ch\u01a1i xe kh\u1ee7ng long", "product_count": 1, "sub_id": "testv28", "product_video_mode": "one_product_per_video"}, "page_ids": []}) or {}
        j4 = (create4.get("job") or {}).get("id")
        if j4:
            self.created_jobs.append(j4)
        self.add("25 created job4 id", bool(j4), str(j4))
        if j4:
            got4 = self.expect("26 get temp job4", "GET", f"/api/jobs/{j4}") or {}
            cfg4 = got4.get("config") or {}
            self.add("27 job4 keyword config", cfg4.get("keyword") == "\u0111\u1ed3 ch\u01a1i xe kh\u1ee7ng long" and int(cfg4.get("product_count") or 0) == 1, str(cfg4)[:220])

        # 28-31 facebook CRUD guard
        pages = self.expect("28 facebook pages list", "GET", "/api/facebook/pages") or []
        page_id = "TEST_PAGE_AUTO_DELETE"
        savep = self.expect("29 save fake facebook page", "POST", "/api/facebook/pages", json={"page_id": page_id, "name": "TEST AUTO DELETE", "access_token": "x" * 20}, status=(200, 400))
        if isinstance(savep, dict) and savep.get("ok"):
            self.created_pages.append(page_id)
            self.expect("30 delete fake facebook page", "DELETE", f"/api/facebook/pages/{page_id}")
        else:
            self.add("30 facebook save guarded", True, "save rejected safely or legacy sync failed")
        self.expect("31 facebook publish jobs", "GET", "/api/facebook/publish-jobs")

        # 28-33 runs/logs/diagnostics/guards
        self.expect("32 runs list", "GET", "/api/runs?limit=5")
        self.expect("33 diagnostics", "GET", "/api/diagnostics")
        self.expect("34 logs", "GET", "/api/logs?limit=20")
        self.expect("35 shopee empty keyword guard", "POST", "/api/shopee/research", status=400, json={"keyword": "", "count": 5})
        self.expect("36 affiliate invalid link guard", "POST", "/api/shopee/affiliate/convert", status=400, json={"links": ["https://example.com/x"], "sub_ids": []})
        queue = flow.get("queue") or {}
        self.add("37 extension connected + compatible", bool(queue.get("extensionConnected") and queue.get("extensionCompatible")), json.dumps({"connected": queue.get("extensionConnected"), "compatible": queue.get("extensionCompatible"), "ext": queue.get("extension"), "min": queue.get("minimumExtensionVersion")}, ensure_ascii=False)[:300])
        diag = self.expect("38 affiliate diag live", "POST", "/api/shopee/affiliate/diag", status=200, json={}, timeout=80) or {}
        self.add("39 affiliate page logged in", bool((diag.get("diag") or {}).get("bodySample") and "Custom Link" in (diag.get("diag") or {}).get("bodySample", "")), json.dumps(diag, ensure_ascii=False)[:300])
        aff_link = "https://shopee.vn/%C4%90%E1%BB%93-ch%C6%A1i-xe-kh%E1%BB%A7ng-long-nu%E1%BB%91t-%C3%B4-t%C3%B4-%C4%91%C6%B0%E1%BB%9Dng-ray-xe-h%C6%A1i-phi%C3%AAn-b%E1%BA%A3n-m%E1%BB%9Bi-c%E1%BB%A1-l%E1%BB%9Bn-cao-c%E1%BA%A5p-gi%C3%A1-r%E1%BA%BB-cho-b%C3%A9-%C4%91%E1%BB%93-ch%C6%A1i-cho-b%C3%A9-trai-MIABABY-i.489501653.27171031836"
        conv = self.expect("40 affiliate convert live", "POST", "/api/shopee/affiliate/convert", status=200, json={"links": [aff_link], "sub_ids": ["testv28"]}, timeout=260) or {}
        items = conv.get("items") or []
        self.add("41 affiliate_url returned", bool(items and items[0].get("affiliate_url")), json.dumps(conv, ensure_ascii=False)[:300])

    def cleanup(self) -> None:
        for jid in reversed(self.created_jobs):
            try:
                r = self.req("DELETE", f"/api/jobs/{jid}")
                self.add(f"cleanup job {jid}", r.status_code in (200, 404), f"HTTP {r.status_code}")
            except Exception as exc:
                self.add(f"cleanup job {jid}", False, str(exc))
        for pid in reversed(self.created_pages):
            try:
                r = self.req("DELETE", f"/api/facebook/pages/{pid}")
                self.add(f"cleanup page {pid}", r.status_code in (200, 404), f"HTTP {r.status_code}")
            except Exception as exc:
                self.add(f"cleanup page {pid}", False, str(exc))

    def report(self) -> int:
        passed = sum(1 for c in self.cases if c.ok)
        failed = len(self.cases) - passed
        print(f"PASS {passed}/{len(self.cases)} | FAIL {failed}")
        for c in self.cases:
            mark = "OK" if c.ok else "FAIL"
            print(f"{mark:4} {c.name} :: {c.detail}")
        return 0 if failed == 0 else 1


def main() -> int:
    t = T()
    try:
        t.run()
    finally:
        t.cleanup()
    return t.report()

if __name__ == "__main__":
    raise SystemExit(main())
