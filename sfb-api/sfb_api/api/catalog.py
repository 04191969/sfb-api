"""Supabase reads and writes for vehicles, parts, fitment, and watches."""

from .catalog_shape import is_uuid
from .supabase_client import get_supabase

PAGE_SIZE = 1000
MAX_PAGES = 30


def save_catalog_import(vehicles, parts, fitment):
    """Upsert a prepared catalog. Vehicles and parts go in before fitment."""
    client = get_supabase()
    for table, rows in (("vehicles", vehicles), ("parts", parts), ("fitment", fitment)):
        for index in range(0, len(rows), 400):
            client.table(table).upsert(rows[index:index + 400], on_conflict="id").execute()
    return {"vehicles": len(vehicles), "parts": len(parts), "fitment": len(fitment)}


def _escape_exact(value):
    """Escape ILIKE wildcards so a model name is matched literally."""
    return (
        str(value)
        .strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def fetch_all(build, page_size=PAGE_SIZE, max_pages=MAX_PAGES):
    rows = []
    for page in range(max_pages):
        start = page * page_size
        batch = build().range(start, start + page_size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows


def _chunks(values, size):
    values = [value for value in dict.fromkeys(values) if value]
    for index in range(0, len(values), size):
        yield values[index:index + size]


def rows_by_id(table, ids, columns="*"):
    found = {}
    client = get_supabase()
    for chunk in _chunks(list(ids), 100):
        result = client.table(table).select(columns).in_("id", chunk).execute()
        for row in result.data or []:
            found[row["id"]] = row
    return found


def vehicle_key_rows(make=None, model=None):
    client = get_supabase()

    def build():
        query = client.table("vehicles").select("make,model,year").order("id")
        if make and str(make).strip():
            query = query.ilike("make", _escape_exact(make))
        if model and str(model).strip():
            query = query.ilike("model", _escape_exact(model))
        return query

    return fetch_all(build)


def search_vehicles(make, model, year):
    client = get_supabase()
    result = (
        client.table("vehicles")
        .select("id,year,make,model,generation,engine,body_style")
        .ilike("make", _escape_exact(make))
        .ilike("model", _escape_exact(model))
        .eq("year", int(year))
        .order("engine")
        .limit(50)
        .execute()
    )
    return result.data or []


def get_vehicle(vehicle_id):
    if not is_uuid(vehicle_id):
        return None
    found = rows_by_id(
        "vehicles",
        [vehicle_id.strip()],
        "id,year,make,model,generation,engine,body_style",
    )
    return found.get(vehicle_id.strip())


def get_part(part_id):
    if not is_uuid(part_id):
        return None
    found = rows_by_id(
        "parts",
        [part_id.strip()],
        "id,part_name,category,oem_part_number,brand",
    )
    return found.get(part_id.strip())


_FITMENT_COLUMNS = "id,part_id,vehicle_id,source,confirmed_at,confidence,qualifier,match_method"
_FITMENT_BASE = "id,part_id,vehicle_id,source,confirmed_at"


def _missing_fitment_column(exc):
    message = str(exc).lower()
    return any(name in message for name in ("confidence", "qualifier", "match_method"))


def fitment_for_vehicle(vehicle_id):
    client = get_supabase()

    def load(columns):
        def build():
            return (
                client.table("fitment")
                .select(columns)
                .eq("vehicle_id", vehicle_id)
                .order("id")
            )

        return fetch_all(build)

    try:
        return load(_FITMENT_COLUMNS)
    except Exception as exc:
        if not _missing_fitment_column(exc):
            raise
        return load(_FITMENT_BASE)


def fitment_for_parts(part_ids):
    client = get_supabase()
    rows = []
    for chunk in _chunks(list(part_ids), 100):
        try:
            result = (
                client.table("fitment")
                .select(_FITMENT_COLUMNS)
                .in_("part_id", chunk)
                .execute()
            )
        except Exception as exc:
            if not _missing_fitment_column(exc):
                raise
            result = (
                client.table("fitment")
                .select(_FITMENT_BASE)
                .in_("part_id", chunk)
                .execute()
            )
        rows.extend(result.data or [])
    return rows


def search_parts(query, tokens):
    from .catalog_parse import category_alias
    from .catalog_shape import present_name, present_category

    client = get_supabase()
    columns = "id,part_name,category,oem_part_number,brand"
    name_tokens = [token for token in tokens if not category_alias(token)]
    categories = []
    for token in tokens:
        alias = category_alias(token)
        if alias and alias not in categories:
            categories.append(alias)
    if name_tokens:
        by_name = client.table("parts").select(columns)
        for token in name_tokens:
            by_name = by_name.ilike("part_name", f"%{_escape_exact(token)}%")
        name_rows = by_name.order("part_name").limit(50).execute().data or []
    else:
        name_rows = (
            client.table("parts").select(columns).order("part_name").limit(300).execute().data or []
        )

    oem_rows = []
    stripped = query.strip()
    if stripped and " " not in stripped:
        oem_rows = (
            client.table("parts")
            .select("id,part_name,category,oem_part_number,brand")
            .ilike("oem_part_number", f"%{_escape_exact(stripped)}%")
            .order("part_name")
            .limit(50)
            .execute()
            .data
            or []
        )

    merged = []
    seen = set()
    for row in name_rows + oem_rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        name = present_name(row.get("part_name"))
        category = present_category(name, row.get("category"))
        if categories and category not in categories:
            continue
        merged.append({**row, "part_name": name, "category": category})
        if len(merged) == 50:
            break
    return merged


def find_vehicles(year, make, model, engine=None):
    client = get_supabase()
    query = (
        client.table("vehicles")
        .select("id,year,make,model,generation,engine,body_style")
        .eq("year", int(year))
        .ilike("make", _escape_exact(make))
        .ilike("model", _escape_exact(model))
        .order("id")
        .limit(20)
    )
    if engine:
        query = query.ilike("engine", _escape_exact(engine))
    return query.execute().data or []


def insert_vehicle(vehicle):
    from uuid import uuid4

    row = {
        "id": str(uuid4()),
        "year": int(vehicle["year"]),
        "make": vehicle["make"].strip(),
        "model": vehicle["model"].strip(),
        "generation": vehicle.get("generation") or None,
        "engine": vehicle.get("engine") or None,
        "body_style": vehicle.get("body_style") or None,
    }
    result = get_supabase().table("vehicles").insert(row).execute()
    return result.data[0] if result.data else row


def find_part(part):
    client = get_supabase()
    columns = "id,part_name,category,oem_part_number,brand"
    oem = part.get("oem_part_number")
    if oem:
        rows = (
            client.table("parts")
            .select(columns)
            .ilike("oem_part_number", _escape_exact(oem))
            .limit(5)
            .execute()
            .data
            or []
        )
        for row in rows:
            if (row.get("oem_part_number") or "").casefold() == oem.casefold():
                return row

    query = client.table("parts").select(columns).ilike("part_name", _escape_exact(part["part_name"]))
    if part.get("category"):
        query = query.ilike("category", _escape_exact(part["category"]))
    rows = query.limit(5).execute().data or []
    category = (part.get("category") or "").casefold()
    for row in rows:
        same_name = (row.get("part_name") or "").casefold() == part["part_name"].casefold()
        same_category = (row.get("category") or "").casefold() == category
        if same_name and same_category:
            return row
    return None


def insert_part(part):
    from uuid import uuid4

    row = {
        "id": str(uuid4()),
        "part_name": part["part_name"],
        "category": part.get("category"),
        "oem_part_number": part.get("oem_part_number"),
        "brand": part.get("brand"),
    }
    result = get_supabase().table("parts").insert(row).execute()
    return result.data[0] if result.data else row


def ensure_fitment(part_id, vehicle_id, source):
    return store_fitment(part_id, vehicle_id, source) == "created"


def refresh_part(existing, incoming):
    """Fill a blank OEM number or brand. A value already stored stays put."""
    updates = {}
    incoming_oem = (incoming.get("oem_part_number") or "").strip()
    incoming_brand = (incoming.get("brand") or "").strip()
    if incoming_oem and not (existing.get("oem_part_number") or "").strip():
        updates["oem_part_number"] = incoming_oem
    if incoming_brand and not (existing.get("brand") or "").strip():
        updates["brand"] = incoming_brand
    if not updates:
        return existing
    get_supabase().table("parts").update(updates).eq("id", existing["id"]).execute()
    return {**existing, **updates}


def store_fitment(part_id, vehicle_id, source, confidence=None, qualifier=None, match_method=None):
    """Insert a fitment row, or raise its confidence when a later pass is stronger.

    A call without a score keeps the original confirmed timestamp. A scored
    call leaves confirmed_at empty until a buyer confirms the fit.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    client = get_supabase()
    columns = "id,confidence" if confidence is not None else "id"
    existing = (
        client.table("fitment")
        .select(columns)
        .eq("part_id", part_id)
        .eq("vehicle_id", vehicle_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        if confidence is None:
            return "exists"
        current = row.get("confidence")
        try:
            higher = current is None or float(confidence) > float(current)
        except (TypeError, ValueError):
            higher = True
        if not higher:
            return "exists"
        update = {
            "confidence": float(confidence),
            "match_method": match_method,
            "source": source,
        }
        if qualifier:
            update["qualifier"] = qualifier
        client.table("fitment").update(update).eq("id", row["id"]).execute()
        return "updated"

    payload = {
        "id": str(uuid4()),
        "part_id": part_id,
        "vehicle_id": vehicle_id,
        "source": source,
        "confirmed_at": None if confidence is not None else datetime.now(timezone.utc).isoformat(),
    }
    if confidence is not None:
        payload["confidence"] = float(confidence)
        payload["qualifier"] = qualifier
        payload["match_method"] = match_method
    client.table("fitment").insert(payload).execute()
    return "created"


def save_collected_part(vehicle, part, source, source_url=""):
    """Dedup the part, write the source car at confidence 1.0, then link stated fits."""
    from .catalog_route import accept_fitment

    existing = find_part(part)
    if existing:
        stored = refresh_part(existing, part)
        part_created = False
    else:
        stored = insert_part(part)
        part_created = True

    fitment_created = 0
    fitment_updated = 0
    status = store_fitment(
        stored["id"],
        vehicle["id"],
        source,
        confidence=1.0,
        match_method=part.get("match_method") or "source_page",
    )
    if status == "created":
        fitment_created += 1
    elif status == "updated":
        fitment_updated += 1

    for other in part.get("also_fits") or []:
        if not accept_fitment(other.get("confidence", 1.0)):
            continue
        for match in find_vehicles(other["year"], other["make"], other["model"]):
            if match["id"] == vehicle["id"]:
                continue
            status = store_fitment(
                stored["id"],
                match["id"],
                source_url or source,
                confidence=other.get("confidence", 1.0),
                qualifier=other.get("qualifier"),
                match_method=other.get("match_method") or "reader_inferred",
            )
            if status == "created":
                fitment_created += 1
            elif status == "updated":
                fitment_updated += 1

    return {
        "part_id": stored["id"],
        "part": stored,
        "part_created": part_created,
        "fitment_created": fitment_created,
        "fitment_updated": fitment_updated,
    }


def known_fits(part_id):
    rows = (
        get_supabase()
        .table("fitment")
        .select("vehicles(year,make,model)")
        .eq("part_id", part_id)
        .limit(50)
        .execute()
        .data
        or []
    )
    return [row["vehicles"] for row in rows if row.get("vehicles")]


def link_inferred_fits(part_id, match, source_url, raw_text=""):
    """Write matcher rows only for vehicles already in the catalog, then log the reason."""
    created = 0
    updated = 0
    first_vehicle = None
    for year in match.get("years") or []:
        for row in find_vehicles(year, match["make"], match["model"]):
            if first_vehicle is None:
                first_vehicle = row["id"]
            status = store_fitment(
                part_id,
                row["id"],
                source_url or "matcher",
                confidence=match.get("confidence"),
                qualifier=match.get("qualifier"),
                match_method=match.get("match_method") or "matcher_cross_ref",
            )
            if status == "created":
                created += 1
            elif status == "updated":
                updated += 1
    if match.get("reasoning"):
        log_match(
            part_id,
            first_vehicle,
            source_url,
            match.get("reasoning"),
            match.get("confidence"),
            raw_text,
        )
    return created, updated


def log_match(part_id, vehicle_id, source_url, reasoning, confidence, raw_text=""):
    from uuid import uuid4

    get_supabase().table("match_log").insert({
        "id": str(uuid4()),
        "part_id": part_id,
        "vehicle_id": vehicle_id,
        "raw_text": (raw_text or "")[:4000],
        "source_url": source_url or "",
        "ai_reasoning": reasoning or "",
        "confidence": confidence,
    }).execute()


def confirm_fitment(fitment_id):
    from datetime import datetime, timezone

    result = (
        get_supabase()
        .table("fitment")
        .update({
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 1.0,
        })
        .eq("id", fitment_id)
        .execute()
    )
    return result.data[0] if result.data else None


def subscription_row(user_id):
    result = (
        get_supabase()
        .table("subscriptions")
        .select("user_id,tier,status,stripe_customer_id,stripe_subscription_id")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def upsert_subscription(row):
    result = (
        get_supabase()
        .table("subscriptions")
        .upsert(row, on_conflict="user_id")
        .execute()
    )
    return result.data[0] if result.data else row


def update_subscription_by_stripe_id(subscription_id, values):
    result = (
        get_supabase()
        .table("subscriptions")
        .update(values)
        .eq("stripe_subscription_id", subscription_id)
        .execute()
    )
    return result.data or []


def bearer_user_id(request):
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    token = header.split(None, 1)[1].strip()
    if not token:
        return None
    user = get_supabase().auth.get_user(token)
    if user is None or getattr(user, "user", None) is None:
        return None
    return user.user.id


def list_watches(user_id):
    result = (
        get_supabase()
        .table("watches")
        .select("id,user_id,part_id,vehicle_id,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return result.data or []


def save_watch(user_id, part_id, vehicle_id=None):
    payload = {"user_id": user_id, "part_id": part_id}
    if vehicle_id:
        payload["vehicle_id"] = vehicle_id
    get_supabase().table("watches").upsert(payload, on_conflict="user_id,part_id").execute()
    result = (
        get_supabase()
        .table("watches")
        .select("id,user_id,part_id,vehicle_id,created_at")
        .eq("user_id", user_id)
        .eq("part_id", part_id)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else payload


def list_watches_by_email(email):
    result = (
        get_supabase()
        .table("watches")
        .select("id,user_id,user_email,part_id,vehicle_id,active,max_price,created_at,last_scanned_at")
        .ilike("user_email", _escape_exact(email))
        .eq("active", True)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
    )
    return result.data or []


def save_email_watch(email, part_id, vehicle_id, max_price=None):
    client = get_supabase()
    existing = (
        client.table("watches")
        .select("id")
        .ilike("user_email", _escape_exact(email))
        .eq("part_id", part_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    payload = {
        "user_email": email,
        "part_id": part_id,
        "vehicle_id": vehicle_id,
        "active": True,
        "max_price": max_price,
    }
    if existing:
        result = client.table("watches").update(payload).eq("id", existing[0]["id"]).execute()
        return result.data[0] if result.data else {**payload, "id": existing[0]["id"]}
    result = client.table("watches").insert(payload).execute()
    return result.data[0] if result.data else payload


def deactivate_watch(watch_id):
    result = (
        get_supabase()
        .table("watches")
        .update({"active": False})
        .eq("id", watch_id)
        .execute()
    )
    return result.data[0] if result.data else None


def list_scan_results(watch_id):
    result = (
        get_supabase()
        .table("scan_results")
        .select("id,watch_id,ebay_item_id,title,price,currency,listing_url,image_url,condition,seller_name,found_at,notified")
        .eq("watch_id", watch_id)
        .order("found_at", desc=True)
        .limit(20)
        .execute()
    )
    return result.data or []


def list_active_watches():
    result = (
        get_supabase()
        .table("watches")
        .select("id,user_email,part_id,vehicle_id,max_price,parts(part_name,oem_part_number),vehicles(year,make,model)")
        .eq("active", True)
        .limit(500)
        .execute()
    )
    return result.data or []


def remember_listing(watch_id, item):
    """Store a listing the first time it is seen. Return False when it is already stored."""
    ebay_id = (item or {}).get("itemId") or ""
    if not ebay_id:
        return False
    client = get_supabase()
    existing = (
        client.table("scan_results")
        .select("id")
        .eq("watch_id", watch_id)
        .eq("ebay_item_id", ebay_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if existing:
        price = (item.get("price") or {}).get("value") or 0
        client.table("scan_results").update({
            "title": item.get("title") or "",
            "price": float(price),
            "currency": (item.get("price") or {}).get("currency") or "USD",
            "listing_url": item.get("itemWebUrl") or "",
            "image_url": (item.get("image") or {}).get("imageUrl") or "",
            "condition": item.get("condition") or "",
            "seller_name": (item.get("seller") or {}).get("username") or "",
        }).eq("id", existing[0]["id"]).execute()
        return False
    price = (item.get("price") or {}).get("value") or 0
    client.table("scan_results").insert({
        "watch_id": watch_id,
        "ebay_item_id": ebay_id,
        "title": item.get("title") or "",
        "price": float(price),
        "currency": (item.get("price") or {}).get("currency") or "USD",
        "listing_url": item.get("itemWebUrl") or "",
        "image_url": (item.get("image") or {}).get("imageUrl") or "",
        "condition": item.get("condition") or "",
        "seller_name": (item.get("seller") or {}).get("username") or "",
        "notified": False,
    }).execute()
    return True


def finish_scan(watch_id, scanned_at):
    get_supabase().table("watches").update({"last_scanned_at": scanned_at}).eq("id", watch_id).execute()
