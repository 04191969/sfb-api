"""Turn catalog HTML into part rows. No network and no database."""

import re
from html import unescape
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup

ROCKAUTO = "rockauto"
SHOWMETHEPARTS = "showmetheparts"
DESIGN911 = "design911"
SOURCES = (ROCKAUTO, SHOWMETHEPARTS, DESIGN911)

_CATEGORIES = (
    ("Brakes", ("brake", "rotor", "caliper", "pad", "disc", "master cylinder")),
    ("Cooling", ("radiator", "water pump", "thermostat", "coolant", "fan")),
    ("Exhaust", ("exhaust", "muffler", "catalytic", "manifold", "header", "o2 sensor", "lambda")),
    ("Fuel System", ("fuel", "injector", "carburetor", "throttle")),
    ("Suspension", ("shock", "strut", "spring", "control arm", "bushing", "sway bar")),
    ("Steering", ("steering", "tie rod", "rack")),
    ("Transmission", ("transmission", "clutch", "flywheel", "cv joint", "axle", "driveshaft")),
    ("Electrical", ("alternator", "starter", "battery", "ignition", "spark plug", "coil", "relay", "sensor", "ecu")),
    ("HVAC", ("hvac", "heater", "blower", "compressor", "condenser", "evaporator", "a/c", "air conditioning")),
    ("Body", ("bumper", "fender", "hood", "door", "mirror", "grille", "panel", "trim", "window", "glass")),
    ("Interior", ("seat", "carpet", "dashboard", "console", "gauge")),
    ("Wheels & Tires", ("wheel", "tire", "rim", "hub", "bearing")),
    ("Engine", ("engine", "piston", "crankshaft", "camshaft", "valve", "timing", "gasket", "oil pump")),
)

_YEAR = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_RANGE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\s*-\s*(19[5-9]\d|20[0-3]\d)\b")


def guess_category(name):
    """Map a part name onto one catalog category. Unknown names stay Other."""
    lowered = (name or "").casefold()
    for category, words in _CATEGORIES:
        if any(word in lowered for word in words):
            return category
    return "Other"


def parse_fitment_text(text):
    """Read '1986 Porsche 944' or 'Porsche 944 1986' into year/make/model rows."""
    cleaned = " ".join(unescape(text or "").split())
    if not cleaned:
        return []
    years = []
    span = _RANGE.search(cleaned)
    if span:
        start, end = int(span.group(1)), int(span.group(2))
        if end < start:
            start, end = end, start
        years = list(range(start, min(end, start + 7) + 1))
        cleaned = _RANGE.sub(" ", cleaned)
    else:
        found = _YEAR.search(cleaned)
        if not found:
            return []
        years = [int(found.group(1))]
        cleaned = _YEAR.sub(" ", cleaned)
    words = [word for word in cleaned.replace(",", " ").split() if word]
    if len(words) < 2:
        return []
    make, model = words[0], " ".join(words[1:])
    return [
        {"year": year, "make": make[:40], "model": model[:80]}
        for year in years
    ]


def _catalog_segments(href):
    path = unescape(unquote(href or "")).split("?", 1)[0]
    marker = "/catalog/"
    if marker not in path:
        return None
    tail = path.split(marker, 1)[1].strip("/")
    if not tail:
        return None
    return [segment for segment in tail.split(",") if segment]


def rockauto_engine_links(html, make, year, model):
    """Engine pages under one year/make/model. The slug matches '3.2L H6'."""
    wanted = (make.casefold(), str(year), model.casefold().replace(" ", "+"))
    found = []
    seen = set()
    for href in _hrefs(html):
        segments = _catalog_segments(href)
        if not segments or len(segments) != 5 or not segments[-1].isdigit():
            continue
        if (segments[0].casefold(), segments[1], segments[2].casefold()) != wanted:
            continue
        engine = segments[3].replace("+", " ")
        url = "https://www.rockauto.com" + href.split("?", 1)[0].replace("&amp;", "&")
        if url in seen:
            continue
        seen.add(url)
        found.append({"engine": engine, "url": url})
    return found


def rockauto_category_links(html):
    """Category pages one level under an engine, such as Brake & Wheel Hub."""
    found = []
    seen = set()
    for href in _hrefs(html):
        segments = _catalog_segments(href)
        if not segments or len(segments) != 6 or segments[-1].isdigit():
            continue
        url = "https://www.rockauto.com" + unescape(href).split("?", 1)[0]
        if url in seen:
            continue
        seen.add(url)
        found.append(url)
    return found


def rockauto_part_links(html):
    """Part-type pages. The last segment is RockAuto's numeric part-type id."""
    found = []
    seen = set()
    for href, label in _labeled_hrefs(html):
        segments = _catalog_segments(href)
        if not segments or len(segments) < 8 or not segments[-1].isdigit():
            continue
        url = "https://www.rockauto.com" + unescape(href).split("?", 1)[0]
        if url in seen:
            continue
        seen.add(url)
        name = label or segments[-2].replace("+", " ")
        category = segments[-3].replace("+", " ")
        found.append({
            "part_name": " ".join(unescape(name).split())[:120],
            "category": guess_category(name),
            "group": " ".join(unescape(category).split()),
            "url": url,
        })
    return found


def parse_rockauto_listing(html, part_name, category):
    """One catalog part for a part-type page. Brand is the first listing's maker."""
    soup = BeautifulSoup(html or "", "html.parser")
    brand = None
    for node in soup.select(".listing-final-manufacturer"):
        text = " ".join(node.get_text(" ", strip=True).split())
        if text:
            brand = text[:80]
            break
    name = " ".join((part_name or "").split())
    if not name:
        return None
    return {
        "part_name": name[:120],
        "category": category or guess_category(name),
        "oem_part_number": None,
        "brand": brand,
        "also_fits": [],
    }


def parse_showmetheparts(html):
    """Part rows when the page actually contains them. The public app shell does not."""
    soup = BeautifulSoup(html or "", "html.parser")
    if soup.select_one("#microloader") and not soup.select(".part-result, .search-result-item"):
        return []
    parts = []
    for row in soup.select(".part-result, .search-result-item"):
        name_node = row.select_one(".part-name, .part-desc")
        if name_node is None:
            continue
        name = " ".join(name_node.get_text(" ", strip=True).split())
        number_node = row.select_one(".part-number")
        number = " ".join(number_node.get_text(" ", strip=True).split()) if number_node else ""
        also_fits = []
        for node in row.select(".also-fits li, .compatible-vehicle"):
            also_fits.extend(parse_fitment_text(node.get_text(" ", strip=True)))
        if name:
            parts.append(_part(name, number, None, also_fits))
    return parts


def parse_design911(html):
    soup = BeautifulSoup(html or "", "html.parser")
    parts = []
    for row in soup.select(".product-item, .search-result"):
        name_node = row.select_one(".product-name, .product-title, h3 a")
        if name_node is None:
            continue
        name = " ".join(name_node.get_text(" ", strip=True).split())
        number_node = row.select_one(".sku, .part-number")
        number = " ".join(number_node.get_text(" ", strip=True).split()) if number_node else ""
        also_fits = []
        for node in row.select(".suitable-for li, .fits-model"):
            also_fits.extend(parse_fitment_text(node.get_text(" ", strip=True)))
        if name:
            parts.append(_part(name, number, None, also_fits))
    return parts


def rockauto_vehicle_url(make, year, model):
    slug = f"{make},{year},{model}".lower().replace(" ", "+")
    return f"https://www.rockauto.com/en/catalog/{slug}"


def design911_url(make, year, model):
    query = f"{make} {model} {year}".strip().replace(" ", "+")
    return f"https://www.design911.co.uk/search/?q={query}"


def absolute_url(base, href):
    return urljoin(base, unescape(href or ""))


def _part(name, oem, brand, also_fits):
    return {
        "part_name": name[:120],
        "category": guess_category(name),
        "oem_part_number": (oem or "")[:80] or None,
        "brand": (brand or "")[:80] or None,
        "also_fits": also_fits[:20],
    }


def _hrefs(html):
    return [match.group(1) for match in re.finditer(r'href="([^"]+)"', html or "", flags=re.I)]


def _labeled_hrefs(html):
    pairs = []
    for match in re.finditer(r"<a\b([^>]*)>(.*?)</a>", html or "", flags=re.I | re.S):
        href_match = re.search(r'href="([^"]+)"', match.group(1), flags=re.I)
        if href_match is None:
            continue
        label = re.sub(r"<[^>]+>", " ", match.group(2))
        pairs.append((unescape(href_match.group(1)), " ".join(label.split())))
    return pairs
