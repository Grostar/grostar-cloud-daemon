from datetime import datetime
import json
import os
import subprocess
import sys
import time
import urllib.request
from zoneinfo import ZoneInfo

FUTBOL_DIR = "/tmp/futbol7"
MADRID_TZ = ZoneInfo("Europe/Madrid")
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "19200"))  # 5h 20m


def gh_api_call(method: str, path: str, payload: dict = None):
    pat = os.environ.get("GH_PAT", "")
    if not pat:
        return 0, {}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        headers={
            "Authorization": f"token {pat}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
            "User-Agent": "GrostarSupervisor24x7",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except Exception:
        return 0, {}


def wait_for_older_daemon_runs_to_finish():
    """
    Si este ciclo fue pre-calentado 85s antes del fin del ciclo anterior, espera a que el ciclo anterior
    termine de cerrar antes de arrancar el polling de Telegram.
    """
    cur_id = str(os.environ.get("CURRENT_RUN_ID", "")).strip()
    if not cur_id:
        return
    for _ in range(28):
        st, data = gh_api_call("GET", "/repos/Grostar/grostar-cloud-daemon/actions/workflows/daemon_24_7.yml/runs?status=in_progress&per_page=8")
        if st != 200:
            break
        runs = data.get("workflow_runs", [])
        older_active = [
            r for r in runs
            if str(r.get("id")) != cur_id and int(r.get("id", 0)) < int(cur_id)
        ]
        if not older_active:
            break
        time.sleep(4)

    try:
        subprocess.run(
            ["git", "pull", "--rebase", "--autostash", "origin", "main"],
            cwd=FUTBOL_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        pass


def trigger_next_daemon_cycle():
    cur_id = str(os.environ.get("CURRENT_RUN_ID", "")).strip()
    st, data = gh_api_call("GET", "/repos/Grostar/grostar-cloud-daemon/actions/workflows/daemon_24_7.yml/runs?per_page=8")
    if st == 200:
        runs = data.get("workflow_runs", [])
        newer_active = any(
            str(r.get("id")) != cur_id
            and r.get("status") in ("queued", "waiting", "in_progress", "pending")
            and (not cur_id or int(r.get("id", 0)) > int(cur_id))
            for r in runs
        )
        if newer_active:
            return True
    st_d, _ = gh_api_call(
        "POST",
        "/repos/Grostar/grostar-cloud-daemon/actions/workflows/daemon_24_7.yml/dispatches",
        {"ref": "main"},
    )
    return st_d in (200, 204)


def ensure_booking_workflow_started_on_time(triggered_dates: set):
    """
    Garantiza 0 segundos de delay en el arranque de la reserva automática los Martes y Domingos a las 21:44:30:
    Dispara el workflow de reserva dentro del orquestador público grostar-cloud-daemon (sin consumo de cuota privada).
    """
    now = datetime.now(MADRID_TZ)
    if now.weekday() not in (1, 6):
        return
    date_key = now.strftime("%Y-%m-%d")
    if date_key in triggered_dates:
        return
    if now.hour == 21 and 44 <= now.minute <= 50:
        st, data = gh_api_call("GET", "/repos/Grostar/grostar-cloud-daemon/actions/workflows/reserva_automatica.yml/runs?per_page=5")
        if st == 200:
            runs = data.get("workflow_runs", [])
            already_running = any(
                r.get("status") in ("in_progress", "queued", "waiting", "pending")
                for r in runs
            )
            if already_running:
                triggered_dates.add(date_key)
                return
        st_d, _ = gh_api_call(
            "POST",
            "/repos/Grostar/grostar-cloud-daemon/actions/workflows/reserva_automatica.yml/dispatches",
            {
                "ref": "main",
                "inputs": {"modo": "auto", "dry_run": "false", "sync_22": "true", "fecha": ""},
            },
        )
        if st_d in (200, 204):
            triggered_dates.add(date_key)
            print(f"[{now.strftime('%H:%M:%S')}] 🚀 Reserva automática disparada vía API sin delay para hoy ({date_key}).", flush=True)


def is_in_critical_window() -> bool:
    """Impide hacer relevo de ciclo durante ventanas críticas de reserva (21:35-22:05) o avisos (14:55-15:05 / 21:55-22:05)."""
    now = datetime.now(MADRID_TZ)
    wd = now.weekday()
    hm = now.hour * 60 + now.minute
    if wd in (1, 6) and (21 * 60 + 35) <= hm <= (22 * 60 + 5):
        return True
    if wd == 4 and (14 * 60 + 55) <= hm <= (15 * 60 + 5):
        return True
    if wd == 5 and (21 * 60 + 55) <= hm <= (22 * 60 + 5):
        return True
    return False


def start_futbol_process():
    env = os.environ.copy()
    env["CLOUD_DAEMON_24_7"] = "true"
    env["TZ"] = "Europe/Madrid"
    env["MAX_RUNTIME_SECONDS"] = str(MAX_RUNTIME_SECONDS + 900)
    env["GITHUB_PAT"] = os.environ.get("GH_PAT", "")
    env["TELEGRAM_BOT_TOKEN"] = os.environ.get("FUTBOL_BOT_TOKEN", "")
    env["TELEGRAM_CHAT_ID"] = os.environ.get("FUTBOL_CHAT_ID", "")
    return subprocess.Popen(
        [sys.executable, "-u", "asistente_telegram.py"],
        cwd=FUTBOL_DIR,
        env=env,
    )


def main():
    wait_for_older_daemon_runs_to_finish()
    print("🟢 Iniciando Supervisor Cloud 24/7 (Fútbol 7 Antequera)...", flush=True)

    p_futbol = start_futbol_process()
    print(f"🟢 Bot Fútbol 7 activo sin colas ni delay (PID={p_futbol.pid})", flush=True)

    start_ts = time.time()
    prewarmed_next = False
    triggered_booking_dates = set()

    try:
        while True:
            elapsed = time.time() - start_ts
            if elapsed >= MAX_RUNTIME_SECONDS and not is_in_critical_window():
                break

            # Pre-calentar el siguiente contenedor 85s antes de cerrar este ciclo (0.0s de corte entre ciclos)
            if not prewarmed_next and elapsed >= (MAX_RUNTIME_SECONDS - 85) and not is_in_critical_window():
                if trigger_next_daemon_cycle():
                    prewarmed_next = True
                    print("🔄 Siguiente contenedor pre-calentándose en paralelo (0s downtime handoff)...", flush=True)

            time.sleep(12)

            if p_futbol.poll() is not None:
                p_futbol = start_futbol_process()

            # Garantizar disparo puntual sin delay de la reserva los Martes y Domingos a las 21:44:30
            ensure_booking_workflow_started_on_time(triggered_booking_dates)
    finally:
        if not prewarmed_next:
            trigger_next_daemon_cycle()
        print("⏳ Cerrando ciclo actual y entregando relevo al contenedor ya pre-calentado...", flush=True)
        try:
            p_futbol.terminate()
            p_futbol.wait(timeout=8)
        except Exception:
            try:
                p_futbol.kill()
            except Exception:
                pass
        print("✅ Relevo completado con 0s de interrupción.", flush=True)


if __name__ == "__main__":
    main()
