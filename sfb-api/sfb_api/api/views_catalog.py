from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .billing import (
    apply_checkout_session,
    apply_subscription_event,
    construct_event,
    create_checkout_session,
    create_portal_session,
)
from .catalog import (
    bearer_user_id,
    fitment_for_parts,
    fitment_for_vehicle,
    get_part,
    get_vehicle,
    list_watches,
    rows_by_id,
    save_watch,
    search_parts,
    search_vehicles,
    subscription_row,
    vehicle_key_rows,
)
from .catalog_shape import (
    cascade,
    clean_tokens,
    cross_car_matches,
    fitment_vehicles,
    group_parts,
    is_uuid,
    name_matches,
    subscription_is_paid,
)
from .growth import AmbiguousVehicle, grow_vehicle
from .views import _server_error


def _paid_denied(request):
    """Block fitment and watches when PAID_GATE is on and the caller is not paid."""
    if not settings.PAID_GATE:
        return None
    try:
        user_id = bearer_user_id(request)
        row = subscription_row(user_id) if user_id else None
    except Exception as exc:
        return _server_error(exc)
    if subscription_is_paid(row):
        return None
    return Response(
        {
            "error": "Cross-car fitment and watches are included with the paid plan.",
            "code": "payment_required",
        },
        status=status.HTTP_402_PAYMENT_REQUIRED,
    )


def _caller_id(request, supplied=None):
    """Prefer the signed-in Supabase user over a user id in the request."""
    token_user = bearer_user_id(request)
    if token_user:
        return token_user
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip()
    return None


@api_view(["GET"])
@permission_classes([AllowAny])
def vehicle_list(request):
    """Cascading dropdowns: makes, then models, then years."""
    make = request.query_params.get("make")
    model = request.query_params.get("model")
    try:
        rows = vehicle_key_rows(make=make, model=model)
    except Exception as exc:
        return _server_error(exc)
    return Response(cascade(rows, make, model))


@api_view(["GET"])
def vehicle_search(request):
    make = request.query_params.get("make", "")
    model = request.query_params.get("model", "")
    year = request.query_params.get("year", "")
    if not str(make).strip() or not str(model).strip() or not str(year).strip():
        return Response(
            {"error": "make, model, and year are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        year_number = int(year)
    except (TypeError, ValueError):
        return Response(
            {"error": "year must be a number"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        vehicles = search_vehicles(make, model, year_number)
    except Exception as exc:
        return _server_error(exc)
    return Response({"vehicles": vehicles, "count": len(vehicles)})


@api_view(["GET"])
def parts_for_vehicle(request):
    vehicle_id = request.query_params.get("vehicle_id", "")
    if not is_uuid(vehicle_id):
        return Response(
            {"error": "vehicle_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        vehicle = get_vehicle(vehicle_id)
        if not vehicle:
            return Response({"error": "Vehicle not found"}, status=status.HTTP_404_NOT_FOUND)
        fitment_rows = fitment_for_vehicle(vehicle_id.strip())
        parts = rows_by_id(
            "parts",
            [row["part_id"] for row in fitment_rows],
            "id,part_name,category,oem_part_number,brand",
        )
    except Exception as exc:
        return _server_error(exc)
    categories, count = group_parts(fitment_rows, parts)
    return Response({"vehicle": vehicle, "count": count, "categories": categories})


@api_view(["GET"])
def part_search(request):
    query = request.query_params.get("q", "")
    tokens = clean_tokens(query)
    if not tokens:
        return Response(
            {"error": "q is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        parts = search_parts(query, tokens)
    except Exception as exc:
        return _server_error(exc)
    return Response({"parts": parts, "count": len(parts)})


@api_view(["GET"])
def fitment_for_part(request):
    denied = _paid_denied(request)
    if denied:
        return denied
    part_id = request.query_params.get("part_id", "")
    if not is_uuid(part_id):
        return Response(
            {"error": "part_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        part = get_part(part_id)
        if not part:
            return Response({"error": "Part not found"}, status=status.HTTP_404_NOT_FOUND)
        fitment_rows = fitment_for_parts([part_id.strip()])
        vehicles = rows_by_id(
            "vehicles",
            [row["vehicle_id"] for row in fitment_rows],
            "id,year,make,model,generation,engine,body_style",
        )
    except Exception as exc:
        return _server_error(exc)
    listed = fitment_vehicles(fitment_rows, vehicles)
    return Response({"part": part, "count": len(listed), "vehicles": listed})


@api_view(["GET"])
def cross_car(request):
    denied = _paid_denied(request)
    if denied:
        return denied
    vehicle_id = request.query_params.get("vehicle_id", "")
    part_name = request.query_params.get("part_name", "")
    if not is_uuid(vehicle_id) or not clean_tokens(part_name):
        return Response(
            {"error": "vehicle_id and part_name are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        vehicle = get_vehicle(vehicle_id)
        if not vehicle:
            return Response({"error": "Vehicle not found"}, status=status.HTTP_404_NOT_FOUND)
        on_vehicle = fitment_for_vehicle(vehicle_id.strip())
        parts = rows_by_id(
            "parts",
            [row["part_id"] for row in on_vehicle],
            "id,part_name,category,oem_part_number,brand",
        )
        matched = [
            part for part in parts.values()
            if name_matches(part.get("part_name"), part_name)
        ]
        shared = fitment_for_parts([part["id"] for part in matched]) if matched else []
        vehicles = rows_by_id(
            "vehicles",
            [row["vehicle_id"] for row in shared],
            "id,year,make,model,generation,engine,body_style",
        )
    except Exception as exc:
        return _server_error(exc)
    matches, count = cross_car_matches(matched, shared, vehicles, vehicle_id.strip())
    return Response({
        "vehicle": vehicle,
        "part_name": part_name,
        "count": count,
        "matches": matches,
    })


@api_view(["GET", "POST"])
def watch(request):
    denied = _paid_denied(request)
    if denied:
        return denied
    try:
        if request.method == "GET":
            user_id = _caller_id(request, request.query_params.get("user_id"))
            if not user_id:
                return Response(
                    {"error": "user_id is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = list_watches(user_id)
            parts = rows_by_id(
                "parts",
                [row["part_id"] for row in rows],
                "id,part_name,category,oem_part_number,brand",
            )
            vehicles = rows_by_id(
                "vehicles",
                [row.get("vehicle_id") for row in rows],
                "id,year,make,model,generation,engine,body_style",
            )
            watches = []
            for row in rows:
                watches.append({
                    **row,
                    "part": parts.get(row.get("part_id")),
                    "vehicle": vehicles.get(row.get("vehicle_id")),
                })
            return Response({"watches": watches, "count": len(watches)})

        part_id = request.data.get("part_id")
        vehicle_id = request.data.get("vehicle_id")
        user_id = _caller_id(request, request.data.get("user_id"))
        if not user_id or not is_uuid(part_id or ""):
            return Response(
                {"error": "user_id and part_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if vehicle_id not in (None, "") and not is_uuid(vehicle_id):
            return Response(
                {"error": "vehicle_id must be a uuid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        part = get_part(part_id)
        if not part:
            return Response({"error": "Part not found"}, status=status.HTTP_404_NOT_FOUND)
        if vehicle_id and not get_vehicle(vehicle_id):
            return Response({"error": "Vehicle not found"}, status=status.HTTP_404_NOT_FOUND)
        saved = save_watch(user_id, part_id.strip(), vehicle_id.strip() if vehicle_id else None)
    except Exception as exc:
        return _server_error(exc)
    saved["part"] = part
    return Response(saved, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def catalog_grow(request):
    """Store parts for one vehicle from structured rows or catalog text."""
    make = request.data.get("make")
    model = request.data.get("model")
    year = request.data.get("year")
    if not isinstance(make, str) or not make.strip() or not isinstance(model, str) or not model.strip():
        return Response(
            {"error": "make, model, and year are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        year_number = int(year)
    except (TypeError, ValueError):
        return Response(
            {"error": "year must be a number"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not 1900 <= year_number <= 2100:
        return Response(
            {"error": "year is out of range"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    parts = request.data.get("parts")
    text = request.data.get("text")
    if parts is None and not (isinstance(text, str) and text.strip()):
        return Response(
            {"error": "Provide parts or text."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    vehicle = {
        "year": year_number,
        "make": make.strip(),
        "model": model.strip(),
        "generation": (request.data.get("generation") or "").strip() or None,
        "engine": (request.data.get("engine") or "").strip() or None,
        "body_style": (request.data.get("body_style") or "").strip() or None,
    }
    try:
        result = grow_vehicle(
            vehicle,
            parts=parts,
            text=text if isinstance(text, str) else None,
            source=(request.data.get("source") or "growth").strip() or "growth",
        )
    except AmbiguousVehicle as exc:
        return Response(
            {
                "error": "Several vehicles match. Pass engine to choose one.",
                "vehicles": exc.vehicles,
            },
            status=status.HTTP_409_CONFLICT,
        )
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return _server_error(exc)
    return Response(result, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def billing_subscription(request):
    try:
        user_id = bearer_user_id(request)
    except Exception as exc:
        return _server_error(exc)
    if not user_id:
        return Response({"error": "Sign in required."}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        row = subscription_row(user_id) or {"tier": "free", "status": "inactive"}
    except Exception as exc:
        return _server_error(exc)
    return Response({
        "user_id": user_id,
        "tier": row.get("tier") or "free",
        "status": row.get("status") or "inactive",
        "paid": subscription_is_paid(row),
    })


@api_view(["POST"])
def billing_checkout(request):
    try:
        user_id = bearer_user_id(request)
    except Exception as exc:
        return _server_error(exc)
    if not user_id:
        return Response({"error": "Sign in required."}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        url = create_checkout_session(user_id)
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
    except Exception as exc:
        return _server_error(exc)
    return Response({"url": url})


@api_view(["POST"])
def billing_portal(request):
    try:
        user_id = bearer_user_id(request)
    except Exception as exc:
        return _server_error(exc)
    if not user_id:
        return Response({"error": "Sign in required."}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        url = create_portal_session(user_id)
    except ValueError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
    except Exception as exc:
        return _server_error(exc)
    return Response({"url": url})


@api_view(["POST"])
@permission_classes([AllowAny])
def stripe_webhook(request):
    """Stripe calls this directly. The signature is the credential, not X-SFB-Key."""
    try:
        event = construct_event(
            request.body,
            request.headers.get("Stripe-Signature", ""),
        )
    except RuntimeError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
    except Exception:
        return Response({"error": "Invalid Stripe signature."}, status=status.HTTP_400_BAD_REQUEST)

    event_type = event["type"] if isinstance(event, dict) else event.type
    data_object = event["data"]["object"] if isinstance(event, dict) else event.data.object
    if not isinstance(data_object, dict):
        data_object = data_object.to_dict() if hasattr(data_object, "to_dict") else dict(data_object)

    try:
        if event_type == "checkout.session.completed":
            apply_checkout_session(data_object)
        elif event_type == "customer.subscription.updated":
            apply_subscription_event(data_object)
        elif event_type == "customer.subscription.deleted":
            apply_subscription_event(data_object, deleted=True)
    except Exception as exc:
        return _server_error(exc)
    return Response({"received": True})
