import json
import os
import subprocess
import time
from datetime import datetime, timezone, timedelta

TZ_BR = timezone(timedelta(hours=-3))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

def curl_probe(url, timeout=15):
    """
    Usa curl com HTTP/2 e TLS moderno para obter código HTTP e latência real,
    evitando falsos positivos de WAF que bloqueiam a assinatura TLS do Python urllib.
    """
    cmd = [
        "curl",
        "-s",
        "-S",
        "-L",
        "--http2",
        "--max-time", str(timeout),
        "--connect-timeout", "10",
        "-A", USER_AGENT,
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "-o", "/dev/null",
        "-w", "%{http_code}|%{time_total}",
        url
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        output = proc.stdout.strip()
        if "|" in output:
            code_str, time_str = output.split("|", 1)
            code = int(code_str)
            latency = int(float(time_str) * 1000)
            return code, latency, proc.stderr.strip()
        return 0, 0, proc.stderr.strip()
    except Exception as e:
        return 0, 0, str(e)

def evaluate_code(code, latency, stype):
    # Códigos 2xx e 3xx indicam resposta ativa do servidor
    if 200 <= code < 400:
        status = "degraded" if latency > 4500 else "operational"
        return {"status": status, "latency": latency, "detail": f"Online (HTTP {code})"}
    
    # Para bancos e governo, 401/403/405/429 significa que o servidor/WAF respondeu ativamente
    if stype == "bank" and code in (401, 403, 405, 429):
        return {"status": "operational", "latency": latency, "detail": f"Online (WAF HTTP {code})"}
    
    # Para serviços web gerais, 401/403 também confirma servidor no ar (ex: login exigido)
    if code in (401, 403, 405):
        return {"status": "operational", "latency": latency, "detail": f"Online (HTTP {code})"}

    # Erros 5xx (500, 502, 503, 504) são quedas reais do servidor
    if code >= 500:
        return {"status": "down", "latency": latency, "detail": f"Erro no Servidor (HTTP {code})"}

    return None

def check_service(service):
    url = service["url"]
    fallback = service.get("fallback_url")
    stype = service.get("type", "http")

    code, latency, err = curl_probe(url)
    result = evaluate_code(code, latency, stype)
    if result:
        return result

    # Se o endpoint principal falhou por bloqueio regional/timeout, tenta o fallback_url
    if fallback:
        fb_code, fb_latency, fb_err = curl_probe(fallback)
        fb_result = evaluate_code(fb_code, fb_latency, stype)
        if fb_result:
            return fb_result
        err = fb_err or err

    reason = f"HTTP {code}" if code > 0 else (err[:50] if err else "Sem resposta / Timeout")
    return {"status": "down", "latency": latency, "detail": f"Fora do ar ({reason})"}

def send_discord_alert(webhook_url, down_services):
    if not webhook_url or not down_services:
        return
    import urllib.request
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
            "url": svc.get("fallback_url") or svc["url"],
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
