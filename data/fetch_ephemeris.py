"""data/fetch_ephemeris.py - Planetary positions (Swiss Ephemeris).

Ye data internet se NAHI aata - hum khud calculate karte hain:
  * Library: pyswisseph (Swiss Ephemeris). Ye NASA JPL DE431/DE441 ephemeris
    par based hai. Astro.com (Astrodienst) ne yahi banaya tha.
  * Default: tropical zodiac (astro.com / astro-seek default), jisse
    Sun longitude 2024-01-01 ko ~280 deg aata hai.
  * Saath me Lahiri sidereal longitude bhi store karte hain (Indian/Vedic
    style), taaki dono use kar sako.
  * Calculation time: har din 12:00 IST = 06:30 UTC.

Bodies: Sun, Moon, Mercury, Venus, Mars, Jupiter, Saturn, Uranus, Neptune,
        Pluto, Rahu (Mean Node), Ketu (Mean Node + 180 deg)
Aspect orb: 5 degrees (config se badal sakte ho).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from app import config

try:
    import swisseph as swe
except Exception as exc:  # noqa: BLE001
    swe = None
    _SWE_IMPORT_ERROR = str(exc)
else:
    _SWE_IMPORT_ERROR = ""

# (display name, swisseph body id) - order matters for pair loops
BODIES: list[tuple[str, int]] = [
    ("Sun", swe.SUN if swe else 0),
    ("Moon", swe.MOON if swe else 1),
    ("Mercury", swe.MERCURY if swe else 2),
    ("Venus", swe.VENUS if swe else 3),
    ("Mars", swe.MARS if swe else 4),
    ("Jupiter", swe.JUPITER if swe else 5),
    ("Saturn", swe.SATURN if swe else 6),
    ("Uranus", swe.URANUS if swe else 7),
    ("Neptune", swe.NEPTUNE if swe else 8),
    ("Pluto", swe.PLUTO if swe else 9),
    ("Rahu", swe.MEAN_NODE if swe else 10),
]
BODY_NAMES = [b[0] for b in BODIES] + ["Ketu"]

SIGNS = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
         "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

NAKSHATRAS = [
    "Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu",
    "Pushya", "Ashlesha", "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta",
    "Chitra", "Swati", "Vishakha", "Anuradha", "Jyeshtha", "Mula", "Purva Ashadha",
    "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha", "Purva Bhadrapada",
    "Uttara Bhadrapada", "Revati",
]

ASPECTS: dict[str, float] = {
    "Conjunction": 0.0, "Sextile": 60.0, "Square": 90.0,
    "Trine": 120.0, "Opposition": 180.0,
}

BACKEND_INFO: dict = {"engine": None, "moshier_fallback": None, "error": _SWE_IMPORT_ERROR}


def init_engine(ephe_path: Path | None = None) -> str:
    """Swiss Ephemeris set karo. .se1 files project folder me ho to best."""
    if swe is None:
        raise RuntimeError(f"pyswisseph not available: {_SWE_IMPORT_ERROR}")
    path = Path(ephe_path or config.EPHEMERIS_DIR)
    config.assert_not_c_drive(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        swe.set_ephe_path(str(path))
    except Exception:  # noqa: BLE001
        swe.set_ephe_path(None)
    # Lahiri ayanamsa for the sidereal columns
    try:
        swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)
    except Exception:  # noqa: BLE001
        pass
    BACKEND_INFO["engine"] = f"swisseph {getattr(swe, 'version', 'unknown')} (tropical + Lahiri sidereal)"
    return BACKEND_INFO["engine"]


def _jd_ist_noon(day: dt.date) -> float:
    """12:00 IST = 06:30 UTC ke liye Julian Day (UT)."""
    hours_ut = (config.EPHEMERIS_HOUR_IST - 5.5) + 24.0 * 0  # 12:00 IST -> 06:30 UT
    return swe.julday(day.year, day.month, day.day, hours_ut, swe.GREG_CAL)


def _flags() -> int:
    return swe.FLG_SWIEPH | swe.FLG_SPEED


def sign_of(lon: float) -> tuple[str, float]:
    lon = lon % 360.0
    idx = int(lon // 30)
    return SIGNS[idx % 12], round(lon - idx * 30.0, 6)


def nakshatra_of(sidereal_lon: float) -> str:
    lon = sidereal_lon % 360.0
    idx = int(lon // (360.0 / 27.0))
    return NAKSHATRAS[min(idx, 26)]


def compute_day(day: dt.date) -> tuple[list[dict], dict[str, float]]:
    """Ek din ke saare bodies. Return (rows, longitudes)."""
    jd = _jd_ist_noon(day)
    rows: list[dict] = []
    lons: dict[str, float] = {}
    speeds: dict[str, float] = {}
    for name, body_id in BODIES:
        (lon, lat, dist, speed, _slat, _sdist), retflag = swe.calc_ut(jd, body_id, _flags())
        BACKEND_INFO["moshier_fallback"] = bool(retflag & swe.FLG_MOSEPH)
        sign, sign_deg = sign_of(lon)
        try:
            (slon, *_rest), _rf = swe.calc_ut(jd, body_id, _flags() | swe.FLG_SIDEREAL)
        except Exception:  # noqa: BLE001
            slon = float("nan")
        rows.append({
            "date": day.isoformat(),
            "body": name,
            "longitude_deg": round(lon, 6),
            "latitude_deg": round(lat, 6),
            "distance_au": round(dist, 9),
            "speed_deg_per_day": round(speed, 6),
            "sign": sign,
            "sign_degree": sign_deg,
            "is_retrograde": bool(speed < 0),
            "longitude_sidereal_deg": round(slon, 6),
            "nakshatra": nakshatra_of(slon),
            "source": config.SOURCE_SWISSEPH,
        })
        lons[name] = lon % 360.0
        speeds[name] = speed
    # Ketu = Rahu + 180 (Mean Node se, exactly)
    ketu_lon = (lons["Rahu"] + 180.0) % 360.0
    ksign, kdeg = sign_of(ketu_lon)
    s_ketu = (rows[-1]["longitude_sidereal_deg"] + 180.0) % 360.0
    rows.append({
        "date": day.isoformat(),
        "body": "Ketu",
        "longitude_deg": round(ketu_lon, 6),
        "latitude_deg": 0.0,
        "distance_au": rows[-1]["distance_au"],
        "speed_deg_per_day": round(speeds["Rahu"], 6),
        "sign": ksign,
        "sign_degree": kdeg,
        "is_retrograde": True,          # mean node always moves retrograde
        "longitude_sidereal_deg": round(s_ketu, 6),
        "nakshatra": nakshatra_of(s_ketu),
        "source": config.SOURCE_SWISSEPH,
    })
    lons["Ketu"] = ketu_lon
    return rows, lons


def angular_separation(a: float, b: float) -> float:
    diff = abs((a % 360.0) - (b % 360.0))
    return 360.0 - diff if diff > 180.0 else diff


def find_aspects(day_iso: str, lons: dict[str, float], speeds: dict[str, float],
                 orb: float | None = None) -> list[dict]:
    """Saare planet-pair aspects (orb ke andar) nikalo."""
    orb = orb if orb is not None else config.ASPECT_ORB_DEGREES
    out: list[dict] = []
    names = list(lons.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            sep = angular_separation(lons[a], lons[b])
            for aspect_name, target in ASPECTS.items():
                diff = abs(sep - target)
                if diff <= orb:
                    # applying = separation target ki taraf badh raha hai
                    try:
                        future = angular_separation(lons[a] + speeds[a], lons[b] + speeds[b])
                        applying = abs(future - target) < diff
                    except Exception:  # noqa: BLE001
                        applying = None
                    out.append({
                        "date": day_iso, "body_a": a, "body_b": b,
                        "aspect": aspect_name, "aspect_angle": target,
                        "separation_deg": round(sep, 6), "orb_deg": round(diff, 6),
                        "is_applying": applying, "source": config.SOURCE_SWISSEPH,
                    })
                    break
    return out


def _moon_phase(elongation: float) -> str:
    """Moon phase name (as a percentage of the 360 deg cycle)."""
    e = elongation % 360.0
    if e < 12 or e > 348:
        return "New Moon"
    if 12 <= e < 78:
        return "Waxing Crescent"
    if 78 <= e < 102:
        return "First Quarter"
    if 102 <= e < 168:
        return "Waxing Gibbous"
    if 168 <= e < 192:
        return "Full Moon"
    if 192 <= e < 258:
        return "Waning Gibbous"
    if 258 <= e < 282:
        return "Last Quarter"
    return "Waning Crescent"


def _unpack_eclipse(result):
    """pyswisseph ka return (retflag, tret) ya (tret, attr) — dono handle karo."""
    if not isinstance(result, (tuple, list)) or len(result) < 2:
        return None
    first, second = result[0], result[1]
    for candidate, other in ((second, first), (first, second)):
        if (isinstance(candidate, (tuple, list)) and candidate
                and isinstance(candidate[0], (int, float)) and candidate[0] > 0):
            return list(candidate)
    return None


def _call_eclipse(fn, jd):
    """Eclipse routine ko safe tareeke se call karo (signature versions alag hain)."""
    for args in ((jd, _flags(), 0, False), (jd, _flags(), 0), (jd, _flags(), 1, False),
                 (jd, _flags(), 1), (jd, _flags())):
        try:
            tret = _unpack_eclipse(fn(*args))
        except Exception:  # noqa: BLE001
            continue
        if tret:
            return tret
    return None


def _try_eclipses(start: dt.date, end: dt.date) -> tuple[list[dict], str]:
    """Asli eclipse dates Swiss Ephemeris se. Na mile to honest 'unavailable'."""
    events: list[dict] = []
    jd_limit = _jd_ist_noon(end) + 1
    specs = [("sol_eclipse_when_glob", "eclipse_solar", "Sun", "solar eclipse"),
             ("lun_eclipse_when", "eclipse_lunar", "Moon", "lunar eclipse")]
    used = 0
    for func_name, event_type, body, label in specs:
        fn = getattr(swe, func_name, None)
        if fn is None:
            continue
        jd = _jd_ist_noon(start) - 1
        for _ in range(400):
            tret = _call_eclipse(fn, jd)
            if not tret or tret[0] <= 0 or tret[0] >= jd_limit:
                break
            y, m, d, _h = swe.revjul(tret[0])
            events.append({"date": f"{y:04d}-{m:02d}-{d:02d}", "event_type": event_type,
                           "body": body, "detail": f"{label} (Swiss Ephemeris, "
                           f"{func_name})", "source": config.SOURCE_SWISSEPH})
            used += 1
            jd = tret[0] + 10.0
    method = (f"swisseph ({used} eclipse events found)" if used
              else "unavailable (eclipse routine returned nothing)")
    return events, method


def detect_events(eph_df: pd.DataFrame) -> pd.DataFrame:
    """Retrograde start/end, sign ingress, New/Full Moon, eclipses."""
    events: list[dict] = []
    if eph_df is None or len(eph_df) == 0:
        return pd.DataFrame()

    for body, grp in eph_df.groupby("body", sort=False):
        grp = grp.sort_values("date")
        prev_retro = None
        prev_sign = None
        for _, row in grp.iterrows():
            retro = bool(row["is_retrograde"])
            sign = str(row["sign"])
            if prev_retro is not None and retro != prev_retro:
                events.append({
                    "date": row["date"],
                    "event_type": "retrograde_start" if retro else "retrograde_end",
                    "body": body,
                    "detail": f"{body} turns {'retrograde' if retro else 'direct'} "
                              f"at {row['longitude_deg']} deg ({sign})",
                    "source": config.SOURCE_SWISSEPH})
            if prev_sign is not None and sign != prev_sign:
                events.append({
                    "date": row["date"], "event_type": "ingress", "body": body,
                    "detail": f"{body} enters {sign} (from {prev_sign})",
                    "source": config.SOURCE_SWISSEPH})
            prev_retro, prev_sign = retro, sign

    # New Moon / Full Moon from Sun-Moon phase
    sun = eph_df[eph_df["body"] == "Sun"].set_index("date")["longitude_deg"]
    moon = eph_df[eph_df["body"] == "Moon"].set_index("date")["longitude_deg"]
    common = sorted(set(sun.index) & set(moon.index))
    prev_phase = None
    for d in common:
        phase = (float(moon[d]) - float(sun[d])) % 360.0
        if prev_phase is not None:
            if prev_phase > 300 and phase < 60:
                events.append({"date": d, "event_type": "new_moon", "body": "Moon",
                               "detail": f"New Moon (Sun-Moon elongation {phase:.2f} deg)",
                               "source": config.SOURCE_SWISSEPH})
            if prev_phase < 180 <= phase:
                events.append({"date": d, "event_type": "full_moon", "body": "Moon",
                               "detail": f"Full Moon (Sun-Moon elongation {phase:.2f} deg)",
                               "source": config.SOURCE_SWISSEPH})
        prev_phase = phase
    return pd.DataFrame(events)


def build_ephemeris(start: dt.date | None = None, end: dt.date | None = None,
                    progress=None) -> dict:
    """2000-01-01 se aaj tak ka poora ephemeris book calculate karo."""
    if swe is None:
        raise RuntimeError(f"pyswisseph not available: {_SWE_IMPORT_ERROR}")
    init_engine()
    start = start or dt.date.fromisoformat(config.EPHEMERIS_START_DATE)
    end = end or config.today_ist()
    days = (end - start).days + 1

    eph_rows: list[dict] = []
    aspect_rows: list[dict] = []
    day = start
    counter = 0
    while day <= end:
        rows, lons = compute_day(day)
        speeds = {r["body"]: r["speed_deg_per_day"] for r in rows}
        eph_rows.extend(rows)
        aspect_rows.extend(find_aspects(day.isoformat(), lons, speeds))
        counter += 1
        if progress and counter % 500 == 0:
            progress(f"ephemeris: {counter}/{days} days "
                     f"({day.isoformat()}), aspects so far={len(aspect_rows)}")
        day += dt.timedelta(days=1)

    eph_df = pd.DataFrame(eph_rows)
    asp_df = pd.DataFrame(aspect_rows)
    evt_df = detect_events(eph_df)
    eclipse_events, eclipse_method = _try_eclipses(start, end)
    if eclipse_events:
        evt_df = pd.concat([evt_df, pd.DataFrame(eclipse_events)], ignore_index=True)
    evt_df = evt_df.sort_values("date").reset_index(drop=True) if len(evt_df) else evt_df
    if progress:
        progress(f"ephemeris done: {len(eph_df)} body-rows, {len(asp_df)} aspects, "
                 f"{len(evt_df)} events (eclipse method: {eclipse_method})")
    return {"ephemeris": eph_df, "aspects": asp_df, "events": evt_df,
            "backend": dict(BACKEND_INFO), "eclipse_method": eclipse_method}


def save_ephemeris(built: dict, register: bool = True) -> dict:
    """Ephemeris parquet files likho + DB event table update karo."""
    from data import normalize  # noqa: PLC0415

    written = {}
    if built.get("ephemeris") is not None and len(built["ephemeris"]):
        written["ephemeris_daily"] = str(normalize.write_dataset(
            "ephemeris_daily", built["ephemeris"], keys=("date", "body"), register=register))
    if built.get("aspects") is not None and len(built["aspects"]):
        written["aspects_daily"] = str(normalize.write_dataset(
            "aspects_daily", built["aspects"],
            keys=("date", "body_a", "body_b", "aspect"), register=register))
    if built.get("events") is not None and len(built["events"]):
        written["events_daily"] = str(normalize.write_dataset(
            "events_daily", built["events"], keys=("date", "event_type", "body"),
            register=register))
        _save_events_to_db(built["events"])
    return written


def _save_events_to_db(events: pd.DataFrame) -> int:
    """Events ko SQLite ephemeris_events table me daalo (duplicate-safe)."""
    try:
        from db import database  # noqa: PLC0415

        conn = database.get_connection()
        try:
            existing = {(r["event_date"], r["event_type"], r["body"]) for r in conn.execute(
                "SELECT event_date, event_type, body FROM ephemeris_events").fetchall()}
            rows = []
            for _, ev in events.iterrows():
                key = (str(ev["date"]), str(ev["event_type"]), str(ev.get("body", "")))
                if key in existing:
                    continue
                rows.append((key[0], key[1], key[2], str(ev.get("detail", "")),
                             str(ev.get("source", config.SOURCE_SWISSEPH))))
            if rows:
                conn.executemany(
                    "INSERT INTO ephemeris_events (event_date, event_type, body, detail, source)"
                    " VALUES (?,?,?,?,?)", rows)
                conn.commit()
            return len(rows)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return 0


def validate_sun_longitude(day: dt.date | None = None) -> dict:
    """PHASE-5 check: Sun ka longitude 2024-01-01 ko ~280 deg hona chahiye."""
    if swe is None:
        return {"ok": False, "error": _SWE_IMPORT_ERROR}
    init_engine()
    day = day or dt.date(2024, 1, 1)
    rows, _ = compute_day(day)
    sun = next(r for r in rows if r["body"] == "Sun")
    moon = next(r for r in rows if r["body"] == "Moon")
    lon = sun["longitude_deg"]
    return {
        "date": day.isoformat(),
        "sun_longitude_deg": lon,
        "sun_sign": sun["sign"],
        "expected_approx": 280.0,
        "within_0p1_of_280": abs(lon - 280.0) <= 1.0,
        "ok": 279.0 <= lon <= 281.0,
        "moon_longitude_deg": moon["longitude_deg"],
        "moon_sign": moon["sign"],
        "moon_phase": _moon_phase((moon["longitude_deg"] - lon) % 360.0),
        "backend": dict(BACKEND_INFO),
    }
