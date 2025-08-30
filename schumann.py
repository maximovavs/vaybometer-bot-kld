#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
schumann.py — сбор и выдача данных для «Шумана» (v2.2)

Возможности:
• Сбор ежечасной точки с безопасным кеш-фоллбэком (минимум null).
• Источники:
  - CUSTOM JSON (SCHU_CUSTOM_URL) — любой JSON, где удаётся найти freq/amp.
  - HeartMath GCI (страница + iframe + JSON), перебор станций GCI001..006:
      * онлайн-страница (SCHU_GCI_URL)
      * прямой iframe (SCHU_GCI_IFRAME)
      * сохранённый HTML (SCHU_HEARTMATH_HTML)
    Можно маппить GCI power → amp (SCHU_MAP_GCI_POWER_TO_AMP=1)
• Запись в файл истории (SCHU_FILE, по умолчанию schumann_hourly.json).
• Forward-fill амплитуды при src=='cache'.
• H7: поля h7_amp/h7_spike оставлены под будущее.
• get_schumann() возвращает freq/amp/trend/status/h7/interpretation.
"""

from __future__ import annotations
import os, sys, re, json, time, calendar
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except Exception:
    requests = None

# ─────── Константы/дефолты ───────

DEF_FILE    = os.getenv("SCHU_FILE", "schumann_hourly.json")
DEF_MAX_LEN = int(os.getenv("SCHU_MAX_LEN", "5000"))
ALLOW_CACHE = os.getenv("SCHU_ALLOW_CACHE_ON_FAIL", "1") == "1"

AMP_SCALE    = float(os.getenv("SCHU_AMP_SCALE", "1"))
TREND_WINDOW = int(os.getenv("SCHU_TREND_WINDOW", "24"))
TREND_DELTA  = float(os.getenv("SCHU_TREND_DELTA", "0.1"))

# Диапазоны значений
FREQ_MIN = float(os.getenv("SCHU_FREQ_MIN", "0"))
FREQ_MAX = float(os.getenv("SCHU_FREQ_MAX", "100"))
AMP_MIN  = float(os.getenv("SCHU_AMP_MIN",  "0"))
AMP_MAX  = float(os.getenv("SCHU_AMP_MAX",  "1000000"))

# Пороги статуса частоты
FREQ_GREEN_MIN = 7.7
FREQ_GREEN_MAX = 8.1
FREQ_RED_MIN   = 7.4
FREQ_RED_MAX   = 8.4

# HeartMath / GCI
GCI_ENABLE     = os.getenv("SCHU_GCI_ENABLE", "0") == "1"
GCI_STATIONS   = [s.strip() for s in os.getenv("SCHU_GCI_STATIONS", "GCI003").split(",") if s.strip()]
GCI_PAGE_URL   = os.getenv("SCHU_GCI_URL", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/")
GCI_IFRAME_URL = os.getenv("SCHU_GCI_IFRAME", "https://www.heartmath.org/gci/gcms/live-data/gcms-magnetometer/power_levels.html")
GCI_SAVED_HTML = os.getenv("SCHU_HEARTMATH_HTML", "")
MAP_GCI_TO_AMP = os.getenv("SCHU_MAP_GCI_POWER_TO_AMP", "0") == "1"

CUSTOM_URL = os.getenv("SCHU_CUSTOM_URL", "").strip()

# H7 placeholders
H7_URL       = os.getenv("H7_URL", "").strip()
H7_TARGET_HZ = float(os.getenv("H7_TARGET_HZ", "54.81"))

DEBUG      = os.getenv("SCHU_DEBUG", "0") == "1"
USER_AGENT = os.getenv("SCHU_USER_AGENT", "Mozilla/5.0 (X11; Linux x86_64)")

# Circuit breaker
BREAKER_FILE      = ".schu_breaker.json"
BREAKER_THRESHOLD = int(os.getenv("SCHU_BREAKER_THRESHOLD", "3"))
BREAKER_COOLDOWN  = int(os.getenv("SCHU_BREAKER_COOLDOWN",  "1800"))

# ─────── Регэкспы ───────
IFRAME_SRC_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']*power_levels\.html[^"\']*)["\']', re.I)
JSON_IN_IFRAME_RE = re.compile(
    r'(?:postMessage\s*\s*(\{.*?\})\s*,|\bvar\s+\w+\s*=\s*(\{.*?\}|\[.*?))',
    re.I | re.S
)

# ─────── Helpers ───────

def _now_hour_ts_utc() -> int:
    t = time.gmtime()
    return int(calendar.timegm((t.tm_year,t.tm_mon,t.tm_mday,t.tm_hour,0,0)))

def _load_history(path: str) -> List[Dict[str, Any]]:
    try:
        return json.load(open(path,encoding="utf-8"))
    except: return []

def _write_history(path: str, items: List[Dict[str, Any]]) -> None:
    tmp = path+".tmp"
    json.dump(items, open(tmp,"w",encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, path)

# ─────── Приоритет источников ───────

def _src_rank(src: str) -> int:
    return {"live":3,"custom":2,"gci_live":2,"gci_saved":2,"gci_iframe":2,"cache":1}.get(str(src),0)

def _better_record(a,b):
    ra,rb=_src_rank(a.get("src")),_src_rank(b.get("src"))
    if ra!=rb: return a if ra>rb else b
    if isinstance(a.get("amp"),(int,float)) and not isinstance(b.get("amp"),(int,float)): return a
    if isinstance(b.get("amp"),(int,float)) and not isinstance(a.get("amp"),(int,float)): return b
    return b

def upsert_record(path,rec,max_len=None):
    try: ts=int(rec.get("ts"))
    except: return
    hist=_load_history(path)
    merged={}
    for r in hist:
        try:t=int(r.get("ts"))
        except:continue
        merged[t]=r if t not in merged else _better_record(merged[t],r)
    merged[ts]=rec if ts not in merged else _better_record(merged[ts],rec)
    out=[merged[t] for t in sorted(merged)]
    if isinstance(max_len,int) and max_len>0 and len(out)>max_len: out=out[-max_len:]
    _write_history(path,out)

def last_known_amp(path):
    for r in reversed(_load_history(path)):
        v=r.get("amp")
        if isinstance(v,(int,float)): return float(v)

# ─────── HTTP ───────
_SESSION=None
def _session():
    global _SESSION
    if _SESSION: return _SESSION
    if not requests: return None
    s=requests.Session()
    try:
        retries=Retry(total=2,connect=2,read=2,backoff_factor=0.6,
                      status_forcelist=(500,502,503,504),
                      allowed_methods=frozenset(["GET"]))
        s.mount("https://",HTTPAdapter(max_retries=retries))
        s.mount("http://",HTTPAdapter(max_retries=retries))
    except: pass
    s.headers.update({"User-Agent":USER_AGENT})
    _SESSION=s; return s

def _get(url,**params):
    s=_session()
    try: return s.get(url,params=params,timeout=15,allow_redirects=True)
    except: return None

# ─────── Circuit breaker ───────
def _breaker_state():
    try: return json.load(open(BREAKER_FILE,"r"))
    except: return {"fail":0,"until":0}
def _breaker_save(st):
    json.dump(st,open(BREAKER_FILE,"w"),ensure_ascii=False)
def breaker_allow(): return time.time()>=_breaker_state().get("until",0)
def breaker_ok(): _breaker_save({"fail":0,"until":0})
def breaker_bad():
    st=_breaker_state(); st["fail"]=st.get("fail",0)+1
    if st["fail"]>=BREAKER_THRESHOLD: st["until"]=int(time.time())+BREAKER_COOLDOWN
    _breaker_save(st)

# ─────── HTML/JSON parse ───────
def extract_iframe_src(html): m=IFRAME_SRC_RE.search(html or ""); return m.group(1) if m else None
def extract_json_from_iframe(html):
    if not html:return None
    for m in JSON_IN_IFRAME_RE.finditer(html):
        block=m.group(1) or m.group(2)
        if not block: continue
        for l in range(len(block),max(len(block)-2000,0),-1):
            try: return json.loads(block[:l])
            except: continue
    return None

def deep_find_number(obj,*keys):
    if obj is None: return None
    if isinstance(obj,list):
        for x in reversed(obj):
            v=deep_find_number(x,*keys)
            if isinstance(v,(int,float)): return float(v)
        return None
    if isinstance(obj,dict):
        for k in keys:
            for kk,vv in obj.items():
                if isinstance(kk,str) and kk.lower()==k.lower():
                    v=deep_find_number(vv,*keys)
                    if isinstance(v,(int,float)): return float(v)
        for st in GCI_STATIONS:
            for kk,vv in obj.items():
                if isinstance(kk,str) and kk.lower()==st.lower():
                    v=deep_find_number(vv,*keys)
                    if isinstance(v,(int,float)): return float(v)
        for vv in obj.values():
            v=deep_find_number(vv,*keys)
            if isinstance(v,(int,float)): return float(v)
        return None
    if isinstance(obj,(int,float)): return float(obj)
    if isinstance(obj,str):
        try:return float(obj.replace(",","."));except: return None
    return None

# ─────── Источники ───────
def get_from_custom():
    if not CUSTOM_URL or not requests: return None,None,"none"
    try:
        r=_get(CUSTOM_URL)
        if not r or r.status_code!=200: return None,None,"custom_fail"
        data=r.json()
    except: return None,None,"custom_fail"
    return deep_find_number(data,"freq"),deep_find_number(data,"amp","amplitude","power"),"custom"

def get_gci_power():
    if not GCI_ENABLE or not requests: return None,"gci_disabled"
    if not breaker_allow(): return None,"gci_circuit_open"
    if GCI_SAVED_HTML:
        try: html=open(GCI_SAVED_HTML,encoding="utf-8").read()
        except: html=None
        if html:
            iframe=extract_iframe_src(html) or GCI_IFRAME_URL
            iframe_html=_get(iframe).text if iframe and _get(iframe) else html
            data=extract_json_from_iframe(iframe_html)
            p=deep_find_number(data,"power","value","amp")
            if isinstance(p,(int,float)): breaker_ok(); return float(p),"gci_saved"
    if GCI_PAGE_URL:
        r=_get(GCI_PAGE_URL)
        if r and r.status_code==200:
            iframe=extract_iframe_src(r.text) or GCI_IFRAME_URL
            rr=_get(iframe)
            if rr and rr.status_code==200:
                data=extract_json_from_iframe(rr.text)
                p=deep_find_number(data,"power","value","amp")
                if isinstance(p,(int,float)): breaker_ok(); return float(p),"gci_live"
    rr=_get(GCI_IFRAME_URL)
    if rr and rr.status_code==200:
        data=extract_json_from_iframe(rr.text)
        p=deep_find_number(data,"power","value","amp")
        if isinstance(p,(int,float)): breaker_ok(); return float(p),"gci_iframe"
    breaker_bad(); return None,"gci_fail"

# ─────── Бизнес-логика ───────
def _clamp_or_none(val,lo,hi):
    try: v=float(val); return v if lo<=v<=hi else None
    except: return None

def collect_once():
    ts=_now_hour_ts_utc()
    freq_val,amp_val,h7_amp,h7_spike,src=None,None,None,None,"none"
    if CUSTOM_URL:
        f,a,src=get_from_custom()
        if f is not None: freq_val=f
        if a is not None: amp_val=a*AMP_SCALE
    if amp_val is None and GCI_ENABLE:
        gci,srcg=get_gci_power()
        if isinstance(gci,(int,float)) and MAP_GCI_TO_AMP:
            amp_val=gci*AMP_SCALE; src=srcg
    if freq_val is None: freq_val=7.83
    freq_val=_clamp_or_none(freq_val,FREQ_MIN,FREQ_MAX) or 7.83
    if amp_val is not None: amp_val=_clamp_or_none(amp_val,AMP_MIN,AMP_MAX)
    if amp_val is None and ALLOW_CACHE:
        amp_prev=last_known_amp(DEF_FILE)
        if amp_prev is not None: amp_val=amp_prev; src="cache"
    return {"ts":ts,"freq":freq_val,"amp":amp_val,"h7_amp":h7_amp,"h7_spike":h7_spike,"ver":2,"src":src}

# ─────── Интерпретации ───────
def classify_freq_status(freq):
    if not isinstance(freq,(int,float)): return "🟡 колебания","yellow"
    if FREQ_RED_MIN<=freq<=FREQ_RED_MAX:
        if FREQ_GREEN_MIN<=freq<=FREQ_GREEN_MAX: return "🟢 в норме","green"
        return "🟡 колебания","yellow"
    return "🔴 сильное отклонение","red"

def trend_human(sym): return {"↑":"растёт","↓":"снижается","→":"стабильно"}.get(sym,"стабильно")
def format_h7(h7,h7s): 
    if isinstance(h7,(int,float)): return f"· H7: {h7:.1f} (⚡ всплеск)" if h7s else f"· H7: {h7:.1f} — спокойно"
    return "· H7: — нет данных"
def gentle_interpretation(code):
    return {"green":"Волны Шумана близки к норме — организм реагирует как на обычный день.",
            "yellow":"Заметны колебания — возможна лёгкая чувствительность к погоде и настроению.",
            "red":"Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки."}.get(code,"")

# ─────── Тренд стрелка ───────
def _trend_arrow(vals,delta=TREND_DELTA):
    if len(vals)<2:return "→"
    last,avg=vals[-1],sum(vals[:-1])/len(vals[:-1])
    if last-avg>=delta: return "↑"
    if last-avg<=-delta: return "↓"
    return "→"

# ─────── get_schumann ───────
def get_schumann():
    hist=_load_history(DEF_FILE)
    if not hist: return {"freq":None,"amp":None,"trend":"→","cached":True}
    freq_series=[r.get("freq") for r in hist if isinstance(r.get("freq"),(int,float))]
    freq_series=freq_series[-max(TREND_WINDOW,2):] if freq_series else []
    trend=_trend_arrow(freq_series) if freq_series else "→"
    last=hist[-1]; freq,amp=last.get("freq"),last.get("amp")
    status,status_code=classify_freq_status(freq)
    return {
        "freq":freq,"amp":amp,
        "trend":trend,"trend_text":trend_human(trend),
        "status":status,"status_code":status_code,
        "h7_text":format_h7(last.get("h7_amp"),last.get("h7_spike")),
        "interpretation":gentle_interpretation(status_code),
        "cached":last.get("src")=="cache",
        "h7_amp":last.get("h7_amp"),"h7_spike":last.get("h7_spike")
    }

# ─────── История ───────
def fix_history(path):
    hist=_load_history(path); old=len(hist)
    by_ts={}
    for r in hist:
        try: ts=int(float(r.get("ts")))
        except: continue
        rr=dict(r)
        f=_clamp_or_none(rr.get("freq"),FREQ_MIN,FREQ_MAX) or 7.83
        a=_clamp_or_none(abs(rr.get("amp")) if isinstance(rr.get("amp"),(int,float)) else None,AMP_MIN,AMP_MAX)
        rr.update(ts=ts,freq=f,amp=a,ver=2,src=rr.get("src") or "cache")
        rr.setdefault("h7_amp",None); rr.setdefault("h7_spike",None)
        by_ts[ts]=rr if ts not in by_ts else _better_record(by_ts[ts],rr)
    cleaned=[by_ts[k] for k in sorted(by_ts)]
    _write_history(path,cleaned); return old,len(cleaned)

# ─────── CLI ───────
def cmd_collect():
    rec=collect_once(); upsert_record(DEF_FILE,rec,DEF_MAX_LEN)
    print(f"collect: ts={rec['ts']} src={rec['src']} freq={rec['freq']} amp={rec['amp']}")
    if rec.get("src")=="cache": print("WARN: cache fallback — live unavailable")
    print("Last record JSON:",json.dumps