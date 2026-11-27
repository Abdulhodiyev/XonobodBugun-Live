import math


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя GPS-точками в километрах."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_nearby_mahallas(
    lat: float,
    lon: float,
    mahallas: list[dict],
    max_radius_km: float,
    limit: int = 4,
) -> list[tuple[dict, float]]:
    """Возвращает ближайшие махалли в пределах радиуса, отсортированные по дистанции."""
    found: list[tuple[dict, float]] = []
    for mh in mahallas:
        center = mh.get("center")
        if not center or len(center) != 2:
            continue
        dist = haversine_km(lat, lon, float(center[0]), float(center[1]))
        if dist <= max_radius_km:
            found.append((mh, dist))
    found.sort(key=lambda pair: pair[1])
    return found[:limit]


def check_target_distance(
    lat: float,
    lon: float,
    center: list[float],
    max_radius_km: float,
) -> tuple[bool, float]:
    dist = haversine_km(lat, lon, float(center[0]), float(center[1]))
    return dist <= max_radius_km, dist