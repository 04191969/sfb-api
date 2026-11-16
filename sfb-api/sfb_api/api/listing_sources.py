"""Places a parts search can read besides the stored catalog.

Craigslist is read from city RSS feeds. Reddit is read from its public JSON
search, and only the subs for that make. Forums are read from Brave's public
search, one site at a time. Facebook uses public search-index item results,
not a feed or authenticated Marketplace access.
"""

import json
import re
from html import unescape
from urllib.parse import urlencode, urlparse
from xml.etree import ElementTree

BRAVE_SEARCH = "https://search.brave.com/search"
RENNLIST_RSS = "https://rennlist.com/forums/external.php?type=rss2"
ROCKAUTO_RSS = "https://www.rockauto.com/RSS/vehiclefeeds.php"
OLD_CARS_LIVE = "https://api.oldcarsdata.com/auctions/live"

UNAVAILABLE = (
    {
        "id": "lkq",
        "name": "LKQ Pick Your Part",
        "reason": "Web search only. No API or RSS feed.",
    },
    {
        "id": "carpart",
        "name": "Car-Part.com",
        "reason": "Recycled-parts inventory is a commercial product. No public API, and the site blocks automated reads.",
    },
    {
        "id": "row52",
        "name": "Row52",
        "reason": "Pick-n-Pull vehicle locator. Yards list cars, not parts.",
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
        {"id": "facebook", "name": "Facebook Marketplace", "method": "site-search", "focus": "Publicly indexed item pages; availability unconfirmed"},
        {"id": "ebay", "name": "eBay", "method": "api", "focus": "Parts category 6030"},
        {"id": "oldcarsdata", "name": "OldCarsData.com", "method": "api", "focus": "Collector auctions"},
        {"id": "rockauto", "name": "RockAuto", "method": "rss", "focus": "Wholesaler closeout"},
        {"id": "craigslist", "name": "Craigslist", "method": "rss", "focus": "Auto parts in 15 cities"},
        {"id": "reddit", "name": "Reddit", "method": "json", "focus": "For-sale posts in make subs"},
        {"id": "rennlist", "name": "Rennlist.com", "method": "rss", "focus": "Porsche parts marketplace"},
    ]
    seen = {row["id"] for row in rows}
    for domain in forum_domains():
        source_id = forum_source_id(domain)
        if source_id in seen:
            continue
        rows.append({
            "id": source_id,
            "name": domain,
            "method": "site-search",
            "focus": "Forum listings for the matching make",
        })
        seen.add(source_id)
    for domain in SHARED_SITES:
        source_id = forum_source_id(domain)
        if source_id in seen:
            continue
        rows.append({
            "id": source_id,
            "name": domain,
            "method": "site-search",
            "focus": "Classified and auction listings on every search",
        })
        seen.add(source_id)
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
    "italian": ["Ferrari", "AlfaRomeo", "Fiat"],
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
    "lincoln": "ford",
    "chevrolet": "gm",
    "chevy": "gm",
    "gmc": "gm",
    "buick": "gm",
    "cadillac": "gm",
    "oldsmobile": "gm",
    "pontiac": "gm",
    "toyota": "toyota",
    "lexus": "toyota",
    "scion": "toyota",
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
    "ferrari": "italian",
    "alfa romeo": "italian",
    "fiat": "italian",
    "maserati": "italian",
    "lamborghini": "italian",
}

FORUM_SITES = {
    "porsche": [
        "rennlist.com", "pelicanparts.com", "planet-9.com", "flatsixes.com",
        "early911sregistry.org", "autokennel.com",
    ],
    "bmw": [
        "bimmerforums.com", "bimmerpost.com", "r3vlimited.com", "bimmerfest.com",
        "mye28.com", "e46fanatics.com",
    ],
    "mercedes": ["mbworld.org", "benzworld.org", "peachparts.com"],
    "ford": [
        "mustangforums.com", "vintage-mustang.com", "stangnet.com",
        "fordtruckenthusiasts.com", "broncocorral.com", "full-size-bronco.com",
        "blueovalforums.com",
    ],
    "gm": [
        "corvetteforums.com", "corvetteforum.com", "camaroz28.com", "gbodyforum.com",
        "nastyz28.com", "67-72chevytrucks.com", "forbodiesonly.com", "trifive.com",
        "forabodiesonly.com",
    ],
    "toyota": [
        "toyotanation.com", "toyota-4runner.org", "tacomaworld.com", "ih8mud.com",
        "supramkiv.com", "mr2oc.com", "clublexus.com",
    ],
    "honda": [
        "hondatech.com", "s2ki.com", "acurazine.com", "honda-acura.net",
        "clubintegra.com",
    ],
    "subaru": ["nasioc.com", "subaruoutback.org", "ft86club.com", "subaruforester.org"],
    "mazda": [
        "clubroadster.net", "miata.net", "rx7club.com", "rx8club.com",
        "mazda3revolution.com",
    ],
    "nissan": [
        "nicoclub.com", "the370z.com", "my350z.com", "zilvia.net",
        "ratsun.net", "classiczcar.com",
    ],
    "vw_audi": [
        "thesamba.com", "vwvortex.com", "audizine.com", "audiworld.com", "tdiclub.com",
    ],
    "jeep": ["jeepforum.com", "wranglerforum.com", "naxja.org", "comancheclub.com"],
    "mopar": [
        "forbbodiesonly.com", "forebodiesonly.com", "dodgetalk.com",
        "turbododge.com", "challengertalk.com", "ramforumz.com",
    ],
    "british": [
        "landroverforums.com", "jaglovers.com", "defender2.net",
        "discoveryowners.com", "jaguarforums.com",
    ],
    "swedish": ["turbobricks.com", "swedespeed.com", "saabcentral.com", "volvoforums.com"],
    "italian": [
        "ferrarichat.com", "ferrarilife.com", "alfabb.com", "fiatforum.com",
        "maseratilife.com",
    ],
    "classic": [
        "grassrootsmotorsports.com", "stovebolt.com", "hagerty.com",
    ],
}

# Classified and auction pages. These run on every search, outside the forum cap.
# Car-Part is a commercial inventory. Row52 lists junk cars, not parts. Neither is called.
SHARED_SITES = (
    "hemmings.com",
    "bringatrailer.com",
)

FORUM_LIMIT = 4

_SALE_TITLE = re.compile(r"\[(?:for sale|fs|wts)\]|\bfor sale\b|\bwts\b|(?:^|\s)fs(?:\s|:|-)", re.I)


def get_subs_for_vehicle(make, model=""):
    """General parts subs, then the make's subs, then classics. Never the whole list.

    model is part of the vehicle call. The published map selects by make.
    """
    subs = list(REDDIT_SUBS["general_parts"])
    category = MAKE_CATEGORIES.get(_make_key(make))
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


def _make_key(make):
    key = " ".join(str(make or "").casefold().replace("-", " ").split())
    if key.startswith("mercedes"):
        key = "mercedes"
    return key


def forum_source_id(domain):
    return domain.split(".")[0].replace("-", "")


def forum_domains():
    seen = []
    for domains in FORUM_SITES.values():
        for domain in domains:
            if domain not in seen:
                seen.append(domain)
    return seen


def get_forum_sites(make):
    """The make's forums, then the shared classic sites. A search reads only the first few."""
    sites = []
    category = MAKE_CATEGORIES.get(_make_key(make))
    if category and category in FORUM_SITES:
        sites.extend(FORUM_SITES[category])
    for domain in FORUM_SITES["classic"]:
        if domain not in sites:
            sites.append(domain)
    return sites


_ANCHOR = re.compile(
    r'<a\b[^>]*\bhref=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_PAIR = re.compile(
    r'"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"\s*,\s*"url"\s*:\s*"(https?:\\?/\\?/[^"]+)"'
    r'|"url"\s*:\s*"(https?:\\?/\\?/[^"]+)"\s*,\s*"title"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"',
    re.S,
)
_TAG = re.compile(r"<[^>]+>")


def brave_forum_url(part_name, domain):
    """One public Brave search for one forum. A combined OR query drops the other sites."""
    query = f'site:{domain} "for sale" {part_name}'
    return BRAVE_SEARCH + "?" + urlencode({"q": query})


def facebook_search_url(part_name, make="", model="", year=""):
    return BRAVE_SEARCH + "?" + urlencode({
        "q": "site:facebook.com/marketplace/item/ " + search_text(part_name, make, model, year)
    })


def _indexed_excerpt(title, item_id, snippet):
    """The result description, or the item name left after the index breadcrumb."""
    text = " ".join(str(snippet or "").split())
    if not text:
        text = " ".join(str(title or "").split())
        text = re.sub(r"^Facebook Marketplace\s+", "", text, flags=re.I)
        text = re.sub(
            r"facebook\.com\s*[›>]\s*marketplace\s*[›>]\s*item\s*[›>]\s*"
            + re.escape(str(item_id))
            + r"\s*",
            "",
            text,
            count=1,
            flags=re.I,
        )
    return text[:1500]


def facebook_results(page, limit=8):
    """Individual indexed items only; snippets never establish live availability."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page or "", "html.parser")
    found, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        parsed = urlparse(anchor["href"])
        if parsed.scheme not in ("http", "https") or parsed.hostname not in (
            "facebook.com", "www.facebook.com", "m.facebook.com"
        ):
            continue
        match = re.fullmatch(r"/marketplace/item/([0-9]+)/?", parsed.path)
        if not match or match[1] in seen:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        block = anchor.find_parent(class_=lambda c: c and "snippet" in c.split())
        description = block.select_one(".snippet-description, .snippet-content .description") if block else None
        snippet = description.get_text(" ", strip=True) if description else ""
        excerpt = _indexed_excerpt(title, match[1], snippet)
        seen.add(match[1])
        found.append({
            "source": "facebook", "item_id": match[1],
            "url": f"https://www.facebook.com/marketplace/item/{match[1]}/",
            "title": (excerpt or title)[:180], "snippet": excerpt,
            "evidence_origin": "public_search_index", "availability": "unconfirmed",
            "source_note": "Search-index excerpt; item page not read; availability unconfirmed. Any displayed price is an asking-price claim.",
            "sale_status": "asking", "identifiers": [],
        })
        if len(found) >= limit:
            break
    return found


def _on_domain(url, domain):
    host = (urlparse(url or "").hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    return host == domain or host.endswith("." + domain)


def _plain(value):
    text = unescape(str(value or "")).replace("\\/", "/")
    return " ".join(_TAG.sub(" ", text).split())


def brave_results(html, domain, limit=3):
    """Listing links on this forum. Other sites on the page are left out."""
    page = re.sub(r"<script\b[^>]*>.*?</script>", " ", html or "", flags=re.I | re.S)
    page = re.sub(r"<style\b[^>]*>.*?</style>", " ", page, flags=re.I | re.S)
    items = []
    seen = set()

    def add(url, title):
        url = _plain(url)
        title = _plain(title)[:180]
        if not title or not _on_domain(url, domain) or url in seen:
            return
        seen.add(url)
        items.append({"title": title, "url": url, "snippet": ""})

    for match in _ANCHOR.finditer(page):
        add(match.group(1), match.group(2))
        if len(items) == limit:
            return items
    for match in _PAIR.finditer(html or ""):
        if match.group(1):
            add(match.group(2), match.group(1))
        else:
            add(match.group(3), match.group(4))
        if len(items) == limit:
            break
    return items


def domain_for_url(url):
    host = (urlparse(url or "").hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
    for domain in forum_domains():
        if host == domain or host.endswith("." + domain):
            return domain
    return ""


def source_for_url(url):
    domain = domain_for_url(url)
    if domain:
        return forum_source_id(domain)
    host = (urlparse(url or "").hostname or "").casefold()
    if host.startswith("www."):
        host = host[4:]
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
