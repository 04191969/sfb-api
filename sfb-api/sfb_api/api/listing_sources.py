"""Places a parts search can read besides the stored catalog.

Craigslist is read from city RSS feeds. Reddit is read from its public JSON
search, and only the subs for that make. A site: search does not see either
one. Facebook Marketplace and LKQ have no public feed, so they are listed
and never fetched.
"""

import json
import re
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

BING_RSS = "https://www.bing.com/search"
RENNLIST_RSS = "https://rennlist.com/forums/external.php?type=rss2"
ROCKAUTO_RSS = "https://www.rockauto.com/RSS/vehiclefeeds.php"
OLD_CARS_LIVE = "https://api.oldcarsdata.com/auctions/live"

# domain, id, name, what that site is for
SITE_SEARCH = (
    ("thesamba.com", "thesamba", "TheSamba.com", "VW and Porsche"),
    ("pelicanparts.com", "pelicanparts", "PelicanParts.com", "Porsche, BMW, and Mercedes"),
    ("bimmerforums.com", "bimmerforums", "BimmerForums.com", "BMW"),
    ("ferrarichat.com", "ferrarichat", "FerrariChat.com", "Ferrari"),
    ("jaglovers.com", "jaglovers", "JagLovers.com", "Jaguar"),
    ("landroverforums.com", "landroverforums", "LandRoverForums.com", "Land Rover"),
    ("hemmings.com", "hemmings", "Hemmings.com", "Classic cars"),
    ("bringatrailer.com", "bringatrailer", "BringATrailer.com", "Collector auctions"),
    ("forabodiesonly.com", "forabodiesonly", "ForABodiesOnly.com", "Mopar A-body"),
    ("grassrootsmotorsports.com", "grassrootsmotorsports", "GrassrootsMotorsports.com", "Enthusiast cars"),
    ("car-part.com", "carpart", "Car-Part.com", "Used and salvage"),
    ("row52.com", "row52", "Row52.com", "Self-service yards"),
)

UNAVAILABLE = (
    {
        "id": "facebook",
        "name": "Facebook Marketplace",
        "reason": "No public API or RSS. Groups are not indexed either.",
    },
    {
        "id": "lkq",
        "name": "LKQ Pick Your Part",
        "reason": "Web search only. No API or RSS feed.",
    },
)


def search_text(part_name, make="", model="", year=""):
    return " ".join(
        str(piece).strip()
        for piece in (part_name, year, make, model)
        if str(piece or "").strip()
    )


def accessible_sources():
    """Sources a search is allowed to call. Unavailable sites are not in this list."""
    rows = [
        {"id": "ebay", "name": "eBay", "method": "api", "focus": "Parts category 6030"},
        {"id": "oldcarsdata", "name": "OldCarsData.com", "method": "api", "focus": "Collector auctions"},
        {"id": "rockauto", "name": "RockAuto", "method": "rss", "focus": "Wholesaler closeout"},
        {"id": "craigslist", "name": "Craigslist", "method": "rss", "focus": "Auto parts in 15 cities"},
        {"id": "reddit", "name": "Reddit", "method": "json", "focus": "For-sale posts in make subs"},
        {"id": "rennlist", "name": "Rennlist.com", "method": "rss", "focus": "Porsche parts marketplace"},
    ]
    rows.extend(
        {"id": source_id, "name": name, "method": "site-search", "focus": focus}
        for _domain, source_id, name, focus in SITE_SEARCH
    )
    return rows


def unavailable_sources():
    return [dict(row) for row in UNAVAILABLE]


def rockauto_rss_url(carcode):
    code = str(carcode or "").strip()
    if not code.isdigit():
        return None
    return ROCKAUTO_RSS + "?" + urlencode({"carcode": code, "m": "wc", "l": "en"})


CL_CITIES = (
    "sfbay", "losangeles", "chicago", "dallas", "houston",
    "miami", "atlanta", "seattle", "denver", "phoenix",
    "detroit", "philadelphia", "boston", "portland", "sandiego",
)


def craigslist_city_url(city, part_name):
    """City auto-parts RSS. The national site: search does not list these posts."""
    return (
        f"https://{city}.craigslist.org/search/pta?"
        + urlencode({"query": part_name, "format": "rss"})
    )


def craigslist_rss_url(part_name, city="sfbay"):
    return craigslist_city_url(city, part_name)


REDDIT_SUBS = {
    "general_parts": [
        "AutoPartsSales", "CarParts", "AutoParts", "carselling",
    ],
    "porsche": [
        "Porsche", "Porsche911", "Porsche944", "Porsche986", "Porsche987",
        "Porsche996", "Porsche997", "Porsche993", "Cayman",
    ],
    "bmw": ["BMW", "E30", "E36", "E46", "E90", "E39", "BMWE36", "BmwTech"],
    "mercedes": ["Mercedes_Benz", "MercedesBenz", "W124", "W201"],
    "ford": [
        "Mustang", "MustangForSale", "FordTrucks", "FordFocus",
        "FordBronco", "CrownVictoria",
    ],
    "gm": [
        "Corvette", "Camaro", "ChevyTrucks", "GMT400", "SquareBody",
        "Silverado", "ForABodiesOnly",
    ],
    "toyota": [
        "Toyota", "ToyotaTacoma", "4Runner", "LandCruisers", "Supra",
        "MR2", "AE86", "Lexus", "LexusIS",
    ],
    "honda": [
        "Honda", "CivicSi", "CivicX", "S2000", "HondaPrelude",
        "Acura", "Acura_RSX",
    ],
    "subaru": ["Subaru", "WRX", "SubaruForester", "BattleWagon", "ft86"],
    "mazda": ["Mazda", "Miata", "MiataMarketplace", "RX7", "RX8", "mazda3", "CX5"],
    "nissan": ["Nissan", "350z", "370z", "240sx", "Datsun", "infiniti", "GTR"],
    "vw_audi": ["Volkswagen", "GolfGTI", "Audi", "MK7GTI", "TDI"],
    "jeep": [
        "Jeep", "JeepGarageSale", "WranglerYJ", "WranglerTJ",
        "CherokeeXJ", "GrandCherokee",
    ],
    "mopar": ["Dodge", "Challenger", "Charger", "Ram_Trucks", "Mopar"],
    "british": ["LandRover", "Defender", "Jaguar"],
    "swedish": ["Volvo", "Saab"],
    "classic": [
        "projectcar", "classiccars", "CollectorCarFeed", "vintagecars",
        "BarrettJackson",
    ],
    "trucks_offroad": ["Trucks", "overlanding", "ToyotaPickup"],
}

MAKE_CATEGORIES = {
    "porsche": "porsche",
    "bmw": "bmw",
    "mercedes": "mercedes",
    "ford": "ford",
    "chevrolet": "gm",
    "chevy": "gm",
    "gmc": "gm",
    "toyota": "toyota",
    "lexus": "toyota",
    "honda": "honda",
    "acura": "honda",
    "subaru": "subaru",
    "mazda": "mazda",
    "nissan": "nissan",
    "infiniti": "nissan",
    "datsun": "nissan",
    "volkswagen": "vw_audi",
    "vw": "vw_audi",
    "audi": "vw_audi",
    "jeep": "jeep",
    "dodge": "mopar",
    "chrysler": "mopar",
    "ram": "mopar",
    "plymouth": "mopar",
    "land rover": "british",
    "landrover": "british",
    "jaguar": "british",
    "volvo": "swedish",
    "saab": "swedish",
}

_SALE_TITLE = re.compile(r"\[(?:for sale|fs|wts)\]|\bfor sale\b|\bwts\b|(?:^|\s)fs(?:\s|:|-)", re.I)


def get_subs_for_vehicle(make, model=""):
    """General parts subs, then the make's subs, then classics. Never the whole list.

    model is part of the vehicle call. The published map selects by make.
    """
    subs = list(REDDIT_SUBS["general_parts"])
    key = " ".join(str(make or "").casefold().replace("-", " ").split())
    if key.startswith("mercedes"):
        key = "mercedes"
    category = MAKE_CATEGORIES.get(key)
    if category:
        subs.extend(REDDIT_SUBS[category])
    subs.extend(REDDIT_SUBS["classic"])
    return subs[:15]


def reddit_search_url(subreddit, part_name):
    return (
        f"https://www.reddit.com/r/{subreddit}/search.json?"
        + urlencode({
            "q": part_name,
            "restrict_sr": "on",
            "sort": "new",
            "limit": 10,
        })
    )


def is_for_sale(title):
    return bool(_SALE_TITLE.search(str(title or "")))


def reddit_posts(payload_text, subreddit):
    """For-sale posts only. A discussion thread is left out."""
    try:
        payload = json.loads(payload_text or "")
    except json.JSONDecodeError:
        return []
    children = []
    if isinstance(payload, dict):
        children = (payload.get("data") or {}).get("children") or []
    items = []
    for post in children:
        data = (post or {}).get("data") or {}
        title = str(data.get("title") or "")
        if not is_for_sale(title):
            continue
        permalink = str(data.get("permalink") or "")
        url = permalink if permalink.startswith("http") else "https://www.reddit.com" + permalink
        items.append({
            "source": "reddit",
            "subreddit": subreddit,
            "title": title[:180],
            "url": url,
            "snippet": " ".join(str(data.get("selftext") or "").split())[:500],
        })
    return items


def bing_site_url(text):
    sites = " OR ".join(f"site:{domain}" for domain, *_rest in SITE_SEARCH)
    return BING_RSS + "?" + urlencode({"format": "rss", "q": f"({sites}) {text}"})


def source_for_url(url):
    host = (urlparse(url or "").hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    for domain, source_id, _name, _focus in SITE_SEARCH:
        if host == domain or host.endswith("." + domain):
            return source_id
    if "craigslist.org" in host:
        return "craigslist"
    if host.endswith("reddit.com"):
        return "reddit"
    if host.endswith("rennlist.com"):
        return "rennlist"
    if "rockauto.com" in host:
        return "rockauto"
    if "ebay." in host:
        return "ebay"
    if "oldcarsdata.com" in host:
        return "oldcarsdata"
    return ""


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def rss_items(xml_text, limit=8):
    """Titles, links, and short descriptions from an RSS or Atom feed."""
    try:
        root = ElementTree.fromstring(xml_text or "")
    except ElementTree.ParseError:
        return []
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = link = description = ""
        for child in list(node):
            name = _local(child.tag)
            value = " ".join((child.text or "").split())
            if name == "title":
                title = value
            elif name == "link":
                link = value or child.attrib.get("href") or ""
            elif name in ("description", "summary"):
                description = value
        if title or link:
            items.append({
                "title": title[:180],
                "url": link,
                "snippet": description[:250],
            })
        if len(items) == limit:
            break
    return items


def keep_listing(item, part_name):
    """A feed item has to mention the part, not merely the car."""
    needles = [word.casefold() for word in str(part_name or "").split() if len(word) > 3]
    if not needles:
        return False
    haystack = f"{item.get('title') or ''} {item.get('snippet') or ''}".casefold()
    return any(word in haystack for word in needles)
