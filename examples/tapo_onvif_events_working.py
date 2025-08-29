#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TAPO ONVIF EVENT LISTENER (PullPoint, con debounce e chiusura sessioni)

Uso:
  python tapo_onvif_events_filtered.py <ip> <user> <password> \
      --port 2020 \
      --wsdl /percorso/al/wsdl \
      --duration 60 \
      --topics "tns1:RuleEngine/CellMotionDetector/Motion:IsMotion,tns1:RuleEngine/PeopleDetector/People:IsPeople"

Note:
- --wsdl è consigliato (punta alla cartella 'onvif/wsdl' del tuo venv o repo)
- --topics può essere omesso: default = Motion (IsMotion) + People (IsPeople)
"""

import argparse
import asyncio
from datetime import datetime, timedelta, UTC
from pathlib import Path
import sys
from typing import Dict, Iterable, Tuple

from onvif import ONVIFCamera, ONVIFError
from onvif.managers import PullPointManager


DEFAULT_FILTERS = [
    ("tns1:RuleEngine/CellMotionDetector/Motion", ["IsMotion"]),
    ("tns1:RuleEngine/PeopleDetector/People", ["IsPeople"]),
]


def parse_topics(s: str | None) -> list[Tuple[str, list[str]]]:
    """
    Converte una stringa tipo:
      "tns1:RuleEngine/CellMotionDetector/Motion:IsMotion,tns1:RuleEngine/PeopleDetector/People:IsPeople"
    in una lista di tuple [(topic, [k1,k2,...]), ...]
    """
    if not s:
        return DEFAULT_FILTERS.copy()
    out: list[Tuple[str, list[str]]] = []
    parts = [p.strip() for p in s.split(",") if p.strip()]
    for p in parts:
        if ":" not in p:
            # se manca ":", consideralo come namespace monco, ma non dovrebbe capitare.
            out.append((p, []))
            continue
        topic, keys = p.split(":", 1)
        keylist = [k.strip() for k in keys.split("/") if k.strip()]
        # supporto anche "topic:IsMotion,IsPeople"
        if len(keylist) == 1 and "," in keylist[0]:
            keylist = [x.strip() for x in keylist[0].split(",") if x.strip()]
        out.append((topic, keylist))
    return out


def pretty_time(dt_obj) -> str:
    try:
        if hasattr(dt_obj, "astimezone"):
            return dt_obj.astimezone(UTC).strftime("%H:%M:%S")
    except Exception:
        pass
    return datetime.now(UTC).strftime("%H:%M:%S")


def iter_notifications(msgs) -> Iterable:
    """
    Normalizza la risposta PullMessages (può essere dict-like o oggetto zeep).
    Ritorna una lista di NotificationMessage (eventualmente vuota).
    """
    if msgs is None:
        return []
    # dict-like
    if isinstance(msgs, dict):
        return msgs.get("NotificationMessage", []) or []
    # oggetto zeep
    try:
        nm = getattr(msgs, "NotificationMessage", None)
        return nm or []
    except Exception:
        return []


async def close_quietly(obj, meth_name: str):
    """Esegue obj.meth_name() se presente, ignorando gli errori."""
    try:
        if obj and hasattr(obj, meth_name):
            m = getattr(obj, meth_name)
            res = m()
            if asyncio.iscoroutine(res):
                await res
    except Exception:
        pass


async def drain_camera(cam: ONVIFCamera):
    """Chiude tutto per non lasciare sessioni HTTP aperte."""
    # Alcuni servizi vanno chiusi singolarmente se li hai creati; qui chiudiamo a scopo precauzionale.
    for svc_name in (
        "devicemgmt",
        "events",
        "media",
        "ptz",
    ):
        try:
            svc = await getattr(cam, f"create_{svc_name}_service")()
            await close_quietly(svc, "close")
        except Exception:
            pass

    # Chiudi eventuale metodo cam.close/async_close
    for n in ("close", "async_close"):
        if hasattr(cam, n):
            try:
                res = getattr(cam, n)()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                pass

    # Chiudi la session del transport (alcune versioni)
    try:
        transport = getattr(cam, "transport", None) or getattr(cam, "_transport", None)
        if transport and hasattr(transport, "session") and transport.session:
            await transport.session.close()
    except Exception:
        pass


async def run_listener(
    ip: str,
    user: str,
    password: str,
    port: int,
    wsdl_dir: Path,
    duration: int,
    filters: list[Tuple[str, list[str]]],
):
    print("============================================================")
    print("🎯 TAPO ONVIF EVENT LISTENER")
    print(f"📷 {ip}:{port}  WSDL: {wsdl_dir}")
    print("🎯 Filtri:")
    for topic, keys in filters:
        print(f"   • {topic}  Keys={','.join(keys) if keys else '(tutti)'}")
    print("============================================================")

    cam = ONVIFCamera(ip, port, user, password, str(wsdl_dir))

    dev = None
    ppm: PullPointManager | None = None
    pull_svc = None

    # Stati per debounce: (topic, key) -> last_value ("true"/"false")
    last_state: Dict[Tuple[str, str], str] = {}
    hits_true = 0

    try:
        # Risolve XAddrs e stampa qualche info
        await cam.update_xaddrs()
        dev = await cam.create_devicemgmt_service()
        try:
            info = await dev.GetDeviceInformation()
            # Oggetto zeep -> dict
            vendor = getattr(info, "Manufacturer", "n/a")
            model = getattr(info, "Model", "n/a")
            fw = getattr(info, "FirmwareVersion", "n/a")
            build = getattr(info, "SerialNumber", "n/a")
            print(f"📦 Device: {vendor} {model} FW:{fw} {build}")
        except Exception:
            pass

        # Proviamo a scoprire l'XAddr Events
        try:
            caps = await dev.GetCapabilities({"Category": "All"})
            events_xaddr = None
            if hasattr(caps, "Events") and hasattr(caps.Events, "XAddr"):
                events_xaddr = caps.Events.XAddr
            print(f"   XAddr Events: {events_xaddr if events_xaddr else '(sconosciuto)'}")
        except Exception:
            pass

        # PullPoint Manager (fa la CreatePullPointSubscription per noi)
        ppm = await cam.create_pullpoint_manager(
            timedelta(seconds=60), subscription_lost_callback=None
        )
        pull_svc = await cam.create_pullpoint_service()
        print("✅ PullPointManager: sottoscrizione creata")

        # Richiesta PullMessages
        req = pull_svc.create_type("PullMessages")
        req.MessageLimit = 10
        req.Timeout = timedelta(seconds=2)

        t0 = datetime.now(UTC)
        while (datetime.now(UTC) - t0).total_seconds() < duration:
            try:
                resp = await pull_svc.PullMessages(req)
            except Exception as e:
                print(f"[WARN] PullMessages errore: {e}; retry...")
                await asyncio.sleep(0.5)
                continue

            for n in iter_notifications(resp):
                # topic
                topic = None
                try:
                    topic = (
                        getattr(n, "Topic", None) or {}
                    ).get("_value_1") if isinstance(getattr(n, "Topic", None), dict) else getattr(getattr(n, "Topic", None), "_value_1", None)
                except Exception:
                    topic = None
                if not topic:
                    continue

                # payload data
                payload = getattr(n, "Message", None)
                payload_val = getattr(payload, "_value_1", {}) if payload else {}
                data = payload_val.get("Data", {}) if isinstance(payload_val, dict) else getattr(payload_val, "Data", {})
                simple_items = []
                try:
                    simple_items = data.get("SimpleItem", []) if isinstance(data, dict) else getattr(data, "SimpleItem", []) or []
                except Exception:
                    simple_items = []

                # timestamp
                ts = payload_val.get("UtcTime") if isinstance(payload_val, dict) else getattr(payload_val, "UtcTime", None)
                ts_str = pretty_time(ts)

                # Applica filtro topic
                filt_keys = None
                for ftopic, fkeys in filters:
                    if ftopic == topic:
                        filt_keys = fkeys or None
                        break
                if filt_keys is None:
                    # topic non filtrato -> ignora
                    continue

                printed_header = False
                for it in simple_items:
                    name = it.get("Name") if isinstance(it, dict) else getattr(it, "Name", None)
                    value = it.get("Value") if isinstance(it, dict) else getattr(it, "Value", None)
                    if not name:
                        continue
                    if filt_keys and name not in filt_keys:
                        continue

                    key = (topic, name)
                    val = str(value).lower()
                    if val != last_state.get(key):
                        if not printed_header:
                            print(f"[{ts_str}] {topic}")
                            printed_header = True
                        print(f"   • {name} = {value}")
                        last_state[key] = val
                        if val == "true":
                            hits_true += 1

            await asyncio.sleep(0.02)  # micro pausa per non saturare

    except ONVIFError as e:
        print(f"❌ Errore ONVIF: {e}")
    finally:
        # Chiusure ordinate per evitare "Unclosed client session"
        try:
            if pull_svc:
                await close_quietly(pull_svc, "close")
        finally:
            try:
                if ppm:
                    await ppm.stop()
            finally:
                try:
                    await drain_camera(cam)
                except Exception:
                    pass

    print("\n📊 RISULTATO")
    print("============")
    print(f"⏱️  Durata: {duration}s")
    print(f"🚨 Eventi TRUE ricevuti: {hits_true}")


async def main_async():
    ap = argparse.ArgumentParser(description="TAPO ONVIF Event Listener (PullPoint)")
    ap.add_argument("ip")
    ap.add_argument("user")
    ap.add_argument("password")
    ap.add_argument("--port", type=int, default=2020)
    ap.add_argument("--wsdl", type=str, required=True, help="Percorso cartella WSDL (es: .../site-packages/onvif/wsdl)")
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument(
        "--topics",
        type=str,
        default=None,
        help='Lista filtrata "topic:key1,key2,...", separati da virgola. '
             'Es: "tns1:RuleEngine/CellMotionDetector/Motion:IsMotion,'
             'tns1:RuleEngine/PeopleDetector/People:IsPeople"',
    )
    args = ap.parse_args()

    wsdl_path = Path(args.wsdl)
    if not wsdl_path.exists():
        print(f"❌ WSDL non trovato: {wsdl_path}")
        sys.exit(2)

    filters = parse_topics(args.topics)

    print(f"🎯 TAPO ONVIF EVENT LISTENER (filtrato)")
    print(f"📷 {args.ip}:{args.port}  WSDL: {wsdl_path}")
    print("🎯 Filtri:")
    for t, ks in filters:
        print(f"   • {t}  Keys={','.join(ks) if ks else '(tutti)'}")
    print("============================================================")

    await run_listener(
        ip=args.ip,
        user=args.user,
        password=args.password,
        port=args.port,
        wsdl_dir=wsdl_path,
        duration=args.duration,
        filters=filters,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n⏹️  Interrotto dall’utente")
        sys.exit(1)