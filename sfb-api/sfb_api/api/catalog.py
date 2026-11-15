"""Supabase reads and writes for vehicles, parts, fitment, and watches."""

from .catalog_shape import is_uuid
from .supabase_client import get_supabase

PAGE_SIZE = 1000
MAX_PAGES = 30


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


def fitment_for_vehicle(vehicle_id):
    client = get_supabase()

    def build():
        return (
            client.table("fitment")
            .select("id,part_id,vehicle_id,source,confirmed_at")
            .eq("vehicle_id", vehicle_id)
            .order("id")
        )

    return fetch_all(build)


def fitment_for_parts(part_ids):
    client = get_supabase()
    rows = []
    for chunk in _chunks(list(part_ids), 100):
        result = (
            client.table("fitment")
            .select("id,part_id,vehicle_id,source,confirmed_at")
            .in_("part_id", chunk)
            .execute()
        )
        rows.extend(result.data or [])
    return rows


def search_parts(query, tokens):
    client = get_supabase()
    by_name = client.table("parts").select("id,part_name,category,oem_part_number,brand")
    for token in tokens:
        by_name = by_name.ilike("part_name", f"%{_escape_exact(token)}%")
    name_rows = by_name.order("part_name").limit(50).execute().data or []

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
        merged.append(row)
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
    from datetime import datetime, timezone
    from uuid import uuid4

    client = get_supabase()
    existing = (
        client.table("fitment")
        .select("id")
        .eq("part_id", part_id)
        .eq("vehicle_id", vehicle_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        return False
    client.table("fitment").insert({
        "id": str(uuid4()),
        "part_id": part_id,
        "vehicle_id": vehicle_id,
        "source": source,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
    return True


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
