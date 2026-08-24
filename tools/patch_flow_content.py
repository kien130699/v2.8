from pathlib import Path

p = Path("modules/flow_content/app.py")
s = p.read_text(encoding="utf-8")
old = """            elif mtype == "VIDEO_DOWNLOAD_URL":
                jid=str(msg.get("jobId") or agent.job_id or "")
                sid=int(msg.get("sceneId") or 0)
                mid=str(msg.get("mediaId") or "")
                signed_url=str(msg.get("url") or "")"""
new = """            elif mtype in {"VIDEO_DOWNLOAD_URL", "VIDEO_DOWNLOAD_URL_READY"}:
                jid=str(msg.get("jobId") or agent.job_id or "")
                sid=int(msg.get("sceneId") or 0)
                mid=str(msg.get("mediaId") or "")
                signed_url=str(msg.get("url") or msg.get("signedUrl") or "")"""

if old in s:
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print("PATCH OK")
else:
    print("ALREADY PATCHED OR NOT MATCHED")
