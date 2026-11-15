"""Places a parts search can read besides the stored catalog.

Two APIs, three feeds, and one Bing search that covers the forum sites.
Facebook Marketplace and LKQ have no public feed, so they are listed and never fetched.
"""

from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

BING_RSS = "https://www.bing.com/search"
CRAIGSLIST_RSS = "https://www.craigslist.org/search/pta"
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
        "reason": "No public API. Meta does not offer this for a parts search.",
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
        {"id": "craigslist", "name": "Craigslist", "method": "rss", "focus": "Auto parts for sale"},
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


def craigslist_rss_url(text):
    return CRAIGSLIST_RSS + "?" + urlencode({"format": "rss", "query": text})


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
