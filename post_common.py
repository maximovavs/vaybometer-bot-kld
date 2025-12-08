def build_message_legacy_evening(region_name: str,
                                 sea_label: str, sea_cities,
                                 other_label: str, other_cities,
                                 tz: Union[pendulum.Timezone, str]) -> str:
    """
    Вечерний пост с детальной информацией о завтрашнем дне.
    Формат: Калининград → Морские города (с волнами и спортом) → 
    Тёплые/холодные → Астро → Главное и забота о себе
    """
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    tz_name = tz_obj.name
    date_local = pendulum.today(tz_obj).add(days=DAY_OFFSET)

    header = f"<b>🌅 {region_name}: погода на завтра ({date_local.format('DD.MM.YYYY')})</b>"

    P: List[str] = [header]

    # ==================== КАЛИНИНГРАД (шапка) ====================
    wm_main = get_weather(KLD_LAT, KLD_LON) or {}
    
    # Используем day_night_stats для полной статистики
    stats = day_night_stats(KLD_LAT, KLD_LON, tz=tz_name)
    t_day_max = stats.get("t_day_max")
    t_night_min = stats.get("t_night_min")
    rh_min = stats.get("rh_min")
    rh_max = stats.get("rh_max")
    
    # Код погоды из daily
    wcarr = (wm_main.get("daily", {}) or {}).get("weathercode", [])
    wcode = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None
    
    # Ветер и давление через обновлённую функцию
    wind_ms, wind_dir_deg, press_val, press_trend = pick_tomorrow_header_metrics(wm_main, tz_obj)

    # Проверка штормовых условий
    storm = storm_flags_for_tomorrow(wm_main, tz_obj)
    gust = storm.get("max_gust_ms")

    # Формируем описание погоды
    desc = code_desc(wcode) or "—"
    
    # Температуры
    temp_txt = (
        f"{t_day_max:.0f}/{t_night_min:.0f}{NBSP}°C"
        if (t_day_max is not None and t_night_min is not None)
        else "н/д"
    )

    # Ветер с порывами
    if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None:
        wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
    elif isinstance(wind_ms, (int, float)):
        wind_txt = f"💨 {wind_ms:.1f} м/с"
    else:
        wind_txt = "💨 н/д"

    if isinstance(gust, (int, float)):
        wind_txt += f" • порывы до {gust:.0f}"

    # Влажность
    rh_txt = ""
    if isinstance(rh_min, (int, float)) and isinstance(rh_max, (int, float)):
        rh_txt = f" • 💧 RH {rh_min:.0f}–{rh_max:.0f}%"

    # Давление
    press_txt = f" • 🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else ""

    kal_line = (
        f"🏙️ Калининград: дн/ночь {temp_txt} • {desc} • {wind_txt}{rh_txt}{press_txt}"
    )

    P.append(kal_line)
    P.append("———")

    # Штормовое предупреждение (если есть)
    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # ==================== МОРСКИЕ ГОРОДА ====================
    temps_sea: Dict[str, Tuple[float, float, int, float | None]] = {}
    sea_lookup: Dict[str, Tuple[float, float]] = {}
    
    for city, (la, lo) in (sea_cities or []):
        sea_lookup[city] = (la, lo)
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        sst_c = get_sst(la, lo)
        temps_sea[city] = (tmax, tmin or tmax, wcx, sst_c)

    if temps_sea:
        P.append(f"🌊 <b>{sea_label}</b>")
        medals = ["🥵", "😊", "🙄", "😮‍💨", "🥶"]
        
        for i, (city, (d, n, wcx, sst_c)) in enumerate(
            sorted(temps_sea.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
        ):
            # Основная строка города
            line = f"{medals[i]} {city}: {d:.0f}/{n:.0f}{NBSP}°C"
            descx = code_desc(wcx)
            if descx:
                line += f" • {descx}"
            if sst_c is not None:
                line += f" • 🌊 {sst_c:.0f}"
            
            # Волны (из Marine API)
            try:
                la, lo = sea_lookup[city]
                wave_h, wave_t = _fetch_wave_for_tomorrow(la, lo, tz_obj)
                if isinstance(wave_h, (int, float)):
                    line += f" • {wave_h:.1f} м"
            except Exception as e:
                if DEBUG_WATER:
                    logging.warning("Wave fetch failed for %s: %s", city, e)
            
            P.append(line)
            
            # Водные активности (только если условия good)
            try:
                la, lo = sea_lookup[city]
                hl = _water_highlights(city, la, lo, tz_obj, sst_c)
                if hl:
                    P.append(f"   {hl}")
            except Exception as e:
                if DEBUG_WATER:
                    logging.exception("water_highlights failed for %s: %s", city, e)

        P.append("———")

    # ==================== ТЁПЛЫЕ/ХОЛОДНЫЕ ГОРОДА ====================
    temps_oth: Dict[str, Tuple[float, float, int]] = {}
    for city, (la, lo) in (other_cities or []):
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None:
            continue
        wcx = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else 0
        temps_oth[city] = (tmax, tmin or tmax, wcx)

    if temps_oth:
        P.append("🔥 <b>Тёплые города, °C (топ-3)</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0], reverse=True)[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.0f}/{n:.0f}{NBSP}°C" + (f" • {descx}" if descx else ""))
        
        P.append("❄️ <b>Холодные города, °C (топ-3)</b>")
        for city, (d, n, wcx) in sorted(temps_oth.items(), key=lambda kv: kv[1][0])[:3]:
            descx = code_desc(wcx)
            P.append(f"   • {city}: {d:.0f}/{n:.0f}{NBSP}°C" + (f" • {descx}" if descx else ""))
        
        P.append("———")

    # ==================== АСТРОСОБЫТИЯ ====================
    # Используем ту же дату, что и для основного прогноза (завтра по местному времени)
    astro_section = build_astro_section(date_local=date_local, tz_local=tz_name)
    if astro_section:
        P.append(astro_section)
        P.append("———")

    # ==================== ВЫВОД ====================
    # Получаем данные для анализа
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
        kp_ts, kp_src = None, "n/d"

    air = get_air(KLD_LAT, KLD_LON) or {}
    schu_state = {} if DISABLE_SCHUMANN else get_schumann_with_fallback()

    P.append("📜 <b>Завтра: главное и забота о себе</b>")
    
    # Используем умную функцию вывода
    conclusion_lines = build_conclusion(kp, ks, air, storm, schu_state)
    P.extend(conclusion_lines)
    
    P.append("———")

    # ==================== РЕКОМЕНДАЦИИ ====================
    P.append("✅ <b>Рекомендации</b>")
    
    # Определяем тему для рекомендаций
    air_bad, air_label, air_reason = _is_air_bad(air)
    kp_val = float(kp) if isinstance(kp, (int, float)) else None
    kp_main = bool(kp_val is not None and kp_val >= 5)
    storm_main = bool(storm.get("warning"))
    schu_main = (schu_state or {}).get("status_code") == "red"
    
    if storm_main:
        theme = "плохая погода"
    elif kp_main:
        theme = "магнитные бури"
    elif air_bad:
        theme = "плохой воздух"
    elif schu_main:
        theme = "волны Шумана"
    else:
        theme = "здоровый день"
    
    # Получаем безопасные рекомендации
    for tip in safe_tips(theme):
        P.append(tip)

    P.append("———")
    
    # Факт дня
    P.append(f"📚 {get_fact(date_local, region_name)}")
    P.append("")
    P.append("#Калининград #погода #здоровье #море")

    return "\n".join(P)


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
# (добавьте эти функции, если их нет в вашем коде)

def _fetch_wave_for_tomorrow(lat: float, lon: float, tz_obj: pendulum.Timezone,
                             prefer_hour: int = 12) -> Tuple[Optional[float], Optional[float]]:
    """Получает данные о волнах из Marine API Open-Meteo."""
    if not requests:
        return None, None
    try:
        url = "https://marine-api.open-meteo.com/v1/marine"
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "wave_height,wave_period",
            "timezone": tz_obj.name,
        }

        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        j = r.json()
        hourly = j.get("hourly") or {}
        times = [pendulum.parse(t) for t in (hourly.get("time") or []) if t]
        idx = _nearest_index_for_day(times, pendulum.now(tz_obj).add(days=1).date(), prefer_hour, tz_obj)
        if idx is None:
            return None, None
        h = hourly.get("wave_height") or []
        p = hourly.get("wave_period") or []
        w_h = float(h[idx]) if idx < len(h) and h[idx] is not None else None
        w_t = float(p[idx]) if idx < len(p) and p[idx] is not None else None
        return w_h, w_t
    except Exception as e:
        logging.warning("marine fetch failed: %s", e)
        return None, None


def _water_highlights(
    city: str,
    la: float,
    lo: float,
    tz_obj: pendulum.Timezone,
    sst_hint: Optional[float] = None
) -> Optional[str]:
    """
    Возвращает строку ТОЛЬКО если условия «good».
    Пример: 🧜‍♂️ Отлично: Кайт/Винг/Винд; SUP @Spot (SE/cross) • гидрокостюм 4/3 мм
    Если good-активностей нет — вернёт None (ничего не печатаем).
    """
    wm = get_weather(la, lo) or {}
    wind_ms, wind_dir, _, _ = pick_tomorrow_header_metrics(wm, tz_obj)
    wave_h, _ = _fetch_wave_for_tomorrow(la, lo, tz_obj)

    # порывы около полудня
    def _gust_at_noon(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[float]:
        hourly = wm.get("hourly") or {}
        times = _hourly_times(wm)
        idx = _nearest_index_for_day(times, pendulum.now(tz).add(days=1).date(), 12, tz)
        arr = _pick(hourly, "windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[])
        if idx is not None and idx < len(arr):
            try:
                return kmh_to_ms(float(arr[idx]))
            except Exception:
                return None
        return None

    gust = _gust_at_noon(wm, tz_obj)

    wind_val = float(wind_ms) if isinstance(wind_ms, (int, float)) else None
    gust_val = float(gust) if isinstance(gust, (int, float)) else None
    card = _cardinal(float(wind_dir)) if isinstance(wind_dir, (int, float)) else None
    shore, shore_src = _shore_class(city, float(wind_dir) if isinstance(wind_dir, (int, float)) else None)

    # Критерии good для каждого вида спорта
    kite_good = False
    if wind_val is not None:
        if KITE_WIND_GOOD_MIN <= wind_val <= KITE_WIND_GOOD_MAX:
            kite_good = True
        if shore == "offshore":
            kite_good = False
        if gust_val and wind_val and (gust_val / max(wind_val, 0.1) > KITE_GUST_RATIO_BAD):
            kite_good = False
        if wave_h is not None and wave_h >= KITE_WAVE_WARN:
            kite_good = False

    sup_good = False
    if wind_val is not None:
        if (wind_val <= SUP_WIND_GOOD_MAX) and (wave_h is None or wave_h <= SUP_WAVE_GOOD_MAX):
            sup_good = True
        if shore == "offshore" and wind_val >= OFFSHORE_SUP_WIND_MIN:
            sup_good = False

    surf_good = False
    if wave_h is not None:
        if SURF_WAVE_GOOD_MIN <= wave_h <= SURF_WAVE_GOOD_MAX and (wind_val is None or wind_val <= SURF_WIND_MAX):
            surf_good = True

    goods: List[str] = []
    if kite_good: goods.append("Кайт/Винг/Винд")
    if sup_good:  goods.append("SUP")
    if surf_good: goods.append("Сёрф")

    # Если good нет — не печатаем ничего
    if not goods:
        if DEBUG_WATER:
            logging.info("WATER[%s]: no good. wind=%s dir=%s wave_h=%s gust=%s shore=%s",
                         city, wind_val, wind_dir, wave_h, gust_val, shore)
        return None

    # Оформляем good с гидриком
    sst = sst_hint if isinstance(sst_hint, (int, float)) else get_sst(la, lo)
    suit_txt = _wetsuit_hint(sst)
    suit_part = f" • {suit_txt}" if suit_txt else ""

    dir_part = f" ({card}/{shore})" if card or shore else ""
    spot_part = f" @{shore_src}" if shore_src and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}") else ""
    env_mark = " (ENV)" if shore_src and str(shore_src).startswith("ENV:") else ""

    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part


def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
    """Подсказка по толщине гидрика по температуре воды (°C)."""
    if not isinstance(sst, (int, float)):
        return None
    t = float(sst)
    if t >= WSUIT_NONE:   return None
    if t >= WSUIT_SHORTY: return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:     return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:     return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:     return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:     return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"
