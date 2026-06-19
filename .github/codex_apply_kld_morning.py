from __future__ import annotations

import base64
import re
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    assert text.count(old) == 1, (path, text.count(old), old[:80])
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "post_common.py",
    "from weather import get_weather",
    "from weather import get_sunrise_sunset, get_weather",
)
replace_once(
    "post_common.py",
    "    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)\n\n    # Воздух",
    """    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)

    # Закат сегодня
    sunset_line = None
    try:
        _sunrise, sunset = get_sunrise_sunset(
            KLD_LAT,
            KLD_LON,
            tz_obj.name,
            DAY_OFFSET,
        )
        if sunset:
            sunset_line = f"🌇 Закат сегодня: {sunset}"
        else:
            logging.info("KLD morning: время заката недоступно")
    except Exception as e:
        logging.info("KLD morning: не удалось получить время заката: %s", e)

    # Воздух""",
)
replace_once(
    "post_common.py",
    "        if uvi_line:\n            P.append(uvi_line)\n    if SHOW_SPACE:",
    "        if uvi_line:\n            P.append(uvi_line)\n    if sunset_line:\n        P.append(sunset_line)\n    if SHOW_SPACE:",
)

replace_once(
    "format_v2.py",
    "    uv = _morning_pick(lines, (\"☀️\", \"🌞\", \"🔥\"))\n    space = [x for x in _morning_pick(lines, (\"🧲\",)) if \"н/д\" not in x]",
    "    uv = _morning_pick(lines, (\"☀️\", \"🌞\", \"🔥\"))\n    sunset = _morning_pick(lines, (\"🌇\",))\n    safecast = [x for x in _morning_pick(lines, (\"🧪\",)) if \"Safecast\" in x]\n    space = [x for x in _morning_pick(lines, (\"🧲\",)) if \"н/д\" not in x]",
)
replace_once(
    "format_v2.py",
    "    if air:\n        out.append(air[0])\n    if space:\n        out.append(_clean_kp_line(space[0]))",
    "    if air:\n        out.append(air[0])\n    if sunset:\n        out.append(sunset[0])\n    if space:\n        out.append(_clean_kp_line(space[0]))\n    if safecast:\n        out.append(safecast[0])",
)
replace_once(
    "format_v2.py",
    "    out.append(\"✅ \" + \" \".join(tips))",
    "    out.append(\"✅ План: \" + \" \".join(tips))",
)

replace_once(
    "safe_test_post.py",
    """    if not line or line in v2_text:
        return v2_text
    return _insert_before_anchor(v2_text, line, ("🌙 <b>Астроритм", "✅ <b>Рекомендации", "📌 <b>Вывод"))""",
    """    if not line:
        return v2_text
    if "Safecast" in v2_text:
        out: list[str] = []
        replaced = False
        for existing in str(v2_text or "").splitlines():
            if not replaced and "Safecast" in existing:
                out.append(line)
                replaced = True
            else:
                out.append(existing)
        return "\n".join(out)
    return _insert_before_anchor(v2_text, line, ("🌙 <b>Астроритм", "✅ <b>Рекомендации", "📌 <b>Вывод"))""",
)

source = Path(".github/workflows/codex_publish_kld_morning_text.yml").read_text(encoding="utf-8")
payloads = re.findall(r"echo '([A-Za-z0-9+/=]+)' \| base64 --decode", source)
Path("tools/test_format_v2_morning_kld.py").write_bytes(base64.b64decode(payloads[1]))
