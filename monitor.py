import json
import os
import time
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

TZ_BR = timezone(timedelta(hours=-3))

def check_service(service):
    url = service["url"]
    stype = service.get("type", "http")
    start = time.time()
    
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (StatusMonitor-Duzanjos/1.0)"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            latency = int((time.time() - start) * 1000)
            code = resp.getcode()
            
            if stype == "statuspage":
                data = json.loads(resp.read().decode("utf-8"))
                indicator = data.get("status", {}).get("indicator", "none")
                desc = data.get("status", {}).get("description", "Operacional")
                if indicator == "none":
                    status = "operational"
                elif indicator == "minor":
                    status = "degraded"
                else:
                    status = "down"
                return {"status": status, "latency": latency, "detail": desc}
            
            status = "degraded" if latency > 2500 else "operational"
            return {"status": status, "latency": latency, "detail": f"HTTP {code}"}
            
    except urllib.error.HTTPError as e:
        latency = int((time.time() - start) * 1000)
        # Muitos bancos bloqueiam User-Agent de bot com 403/401, o que significa que o servidor está online
        if e.code in (401, 403, 405):
            return {"status": "operational", "latency": latency, "detail": f"Online (HTTP {e.code})"}
        return {"status": "down", "latency": latency, "detail": f"Erro HTTP {e.code}"}
    except Exception as e:
        return {"status": "down", "latency": 0, "detail": str(e)[:60]}

def send_discord_alert(webhook_url, down_services):
    if not webhook_url or not down_services:
        return
    lines = [f"🔴 **{s['name']}** ({s['detail']})" for s in down_services]
    payload = {
        "content": "🚨 **ALERTA DE INDISPONIBILIDADE DETECTADO!**\n" + "\n".join(lines)
    }
    req = urllib.request.Request(
        webhook_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "StatusMonitor/1.0"}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("Falha ao enviar webhook:", e)

def main():
    with open("services.json", "r", encoding="utf-8") as f:
        services = json.load(f)

    # Carrega estado anterior para evitar spam de alertas repetidos
    previous_states = {}
    if os.path.exists("status.json"):
        try:
            with open("status.json", "r", encoding="utf-8") as f:
                old_data = json.load(f)
                previous_states = {s["id"]: s["status"] for s in old_data.get("services", [])}
        except Exception:
            pass

    results = []
    newly_down = []

    for svc in services:
        res = check_service(svc)
        entry = {
            "id": svc["id"],
            "name": svc["name"],
            "category": svc["category"],
            "url": svc["url"],
            "status": res["status"],
            "latency": res["latency"],
            "detail": res["detail"]
        }
        results.append(entry)

        if res["status"] == "down" and previous_states.get(svc["id"]) != "down":
            newly_down.append(entry)

    output = {
        "updated_at": datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M:%S"),
        "services": results
    }

    with open("status.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    webhook = os.environ.get("DISCORD_WEBHOOK", "").strip()
    send_discord_alert(webhook, newly_down)

if __name__ == "__main__":
    main()
