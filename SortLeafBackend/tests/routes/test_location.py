import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from models.location_models import RecyclingPoint
from tests.factories import LocationDataFactory


def test_get_recycling_points_success(client):
    # Use factory to create recycling point data
    point_data = LocationDataFactory.create_recycling_point(
        id="node_1",
        name="Point 1",
        latitude=40.0,
        longitude=-3.0,
        distance=100.0,
        types=["plastic"]
    )
    
    mock_points = [RecyclingPoint(**point_data)]
    
    mock_service = MagicMock()
    mock_service.get_recycling_points = AsyncMock(return_value=mock_points)
    
    coords = LocationDataFactory.create_coordinates(latitude=40.0, longitude=-3.0)
    
    with patch("routes.location.get_location_service", return_value=mock_service):
        response = client.get(
            f"/location/recycling-points?latitude={coords['latitude']}&longitude={coords['longitude']}",
            headers={"X-API-Key": "test-api-key"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["points"]) == 1
        assert data["points"][0]["name"] == "Point 1"


def test_get_recycling_points_invalid_coords(client):
    # Invalid latitude (> 90)
    response = client.get(
        "/location/recycling-points?latitude=100.0&longitude=-3.0",
        headers={"X-API-Key": "test-api-key"}
    )
    assert response.status_code == 422


def test_search_recycling_points_post(client):
    mock_points = []
    mock_service = MagicMock()
    mock_service.get_recycling_points = AsyncMock(return_value=mock_points)
    
    coords = LocationDataFactory.create_coordinates(location_name="madrid")
    
    with patch("routes.location.get_location_service", return_value=mock_service):
        response = client.post(
            "/location/recycling-points/search",
            json={
                "latitude": coords["latitude"],
                "longitude": coords["longitude"],
                "radius": 1000
            },
            headers={"X-API-Key": "test-api-key"}
        )
        
        assert response.status_code == 200
        assert response.json()["count"] == 0


def test_map_returns_nearest_ten_matching_material_and_name(client, monkeypatch):
    from datetime import datetime
    from services import location_service

    elements = [
        {"type": "node", "id": n, "lat": 10.0 + n * .0001, "lon": -84.0,
         "tags": {"amenity": "recycling", "name": "Estación " + str(n), "recycling:glass": "yes"}}
        for n in reversed(range(14))
    ]
    elements.append({"type": "node", "id": 99, "lat": 10.0, "lon": -84.0,
                     "tags": {"amenity": "recycling", "name": "Estación general"}})
    monkeypatch.setitem(location_service._cache, location_service._get_cache_key(10.0, -84.0, 2000),
                        (elements, datetime.now()))
    response = client.get("/location/recycling-points", params={
        "latitude": 10, "longitude": -84, "limit": 10, "types": "glass", "q": "estacion", "radius": 2000,
    }, headers={"X-API-Key": "test-api-key"})
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 10
    assert [p["name"] for p in result["points"]] == ["Estación " + str(n) for n in range(10)]


def test_auto_search_expands_until_ten_filtered_matches(client, monkeypatch):
    from datetime import datetime
    from services import location_service

    # Unsorted provider data. Only two paper depots are in the first 2 km.
    centers = [0.006, 0.012] + [i / 1000 for i in range(25, 35)]
    depots = [
        {"type": "way", "id": i, "center": {"lat": offset, "lon": 0.0},
         "tags": {"amenity": "recycling", "name": f"Depósito {i}", "recycling:paper": "yes"}}
        for i, offset in enumerate(centers)
    ]
    nearby = [
        {"type": "node", "id": i, "lat": 0.0001, "lon": 0.0,
         "tags": {"name": "Depósito general", "amenity": "recycling"}} for i in range(12)
    ] + [{"type": "node", "id": 15, "lat": 0.0001, "lon": 0.0,
          "tags": {"name": "Otra estación", "recycling:paper": "yes"}}]
    for radius, elements in [(2000, nearby + depots[:2]), (10000, nearby + depots[::-1])]:
        monkeypatch.setitem(location_service._cache, (0, 0, radius), (elements, datetime.now()))
    service = location_service.get_location_service()
    with patch.object(service, "get_recycling_points", wraps=service.get_recycling_points) as search:
        response = client.get("/location/recycling-points", params={
            "latitude": 0, "longitude": 0, "types": "paper", "q": "deposito",
        })
    assert response.status_code == 200
    assert [call.kwargs["radius"] for call in search.await_args_list] == [2000, 10000]
    result = response.json()
    assert result["radius"] == 10000 and not result["partial"]
    assert [p["id"] for p in result["points"]] == [f"way_{i}" for i in range(10)]


def test_sparse_auto_search_finds_tenth_point_beyond_fifty_km(client):
    points = [RecyclingPoint(**LocationDataFactory.create_recycling_point(
        id=f"node_{i}", distance=i * 9000 + 100)) for i in range(10)]
    async def nearby(**kwargs):
        return [point for point in points if point.distance <= kwargs["radius"]]
    service = MagicMock()
    service.get_recycling_points = AsyncMock(side_effect=nearby)
    with patch("routes.location.get_location_service", return_value=service):
        data = client.get("/location/recycling-points", params={"latitude": 40, "longitude": -3}).json()
    assert data["count"] == 10 and not data["partial"]
    assert data["points"][-1]["distance"] == 81100


def test_sparse_auto_search_exhausts_the_world_if_it_never_finds_ten(client):
    from services.location_service import WORLD_DISTANCE
    service = MagicMock()
    service.get_recycling_points = AsyncMock(return_value=[])
    with patch("routes.location.get_location_service", return_value=service):
        result = client.get("/location/recycling-points", params={"latitude": 40, "longitude": -3}).json()
    radii = [c.kwargs["radius"] for c in service.get_recycling_points.await_args_list]
    assert radii == sorted(set(radii)) and radii[-1] == WORLD_DISTANCE
    assert result["count"] == 0 and not result["partial"]


def test_dense_provider_batch_is_refined_before_reporting_nearest(client):
    from services.location_service import CrowdedSearch
    points = [RecyclingPoint(**LocationDataFactory.create_recycling_point(
        id=f"node_{i}", distance=3000 + i)) for i in range(10)]
    service = MagicMock()
    service.get_recycling_points = AsyncMock(side_effect=[[], CrowdedSearch(), points])
    with patch("routes.location.get_location_service", return_value=service):
        result = client.get("/location/recycling-points", params={"latitude": 40, "longitude": -3}).json()
    assert result["count"] == 10 and not result["partial"]
    assert [c.kwargs["radius"] for c in service.get_recycling_points.await_args_list] == [2000, 10000, 6000]


def test_provider_failure_during_expansion_preserves_verified_matches(client):
    from exceptions.location_exceptions import OverpassAPIError
    point = RecyclingPoint(**LocationDataFactory.create_recycling_point())
    service = MagicMock()
    service.get_recycling_points = AsyncMock(side_effect=[[point], OverpassAPIError()])
    with patch("routes.location.get_location_service", return_value=service):
        response = client.get("/location/recycling-points", params={"latitude": 40, "longitude": -3})
    assert response.status_code == 200
    result = response.json()
    assert result["count"] == 1 and result["radius"] == 2000 and result["partial"]


def test_san_jose_has_ten_real_ranked_points_without_network(client):
    service = MagicMock()
    with patch("routes.location.get_location_service", return_value=service):
        data = client.get("/location/recycling-points", params={"latitude": 9.9325, "longitude": -84.0796}).json()
    assert data["catalog_date"] and not data["partial"]
    assert data["count"] == len({point["id"] for point in data["points"]}) == 10
    distances = [point["distance"] for point in data["points"]]
    assert distances == sorted(distances)
    service.get_recycling_points.assert_not_called()


def test_rare_material_retains_distant_catalog_matches_if_live_provider_fails(client):
    from exceptions.location_exceptions import OverpassAPIError
    with patch("routes.location.get_location_service") as provider:
        provider.return_value.get_recycling_points = AsyncMock(side_effect=OverpassAPIError())
        data = client.get("/location/recycling-points", params={
            "latitude": 9.9325, "longitude": -84.0796, "types": "glass",
        }).json()
    assert 0 < data["count"] < 10 and data["partial"]
    assert data["catalog_date"] and all("glass" in p["types"] for p in data["points"])
    assert data["points"][-1]["distance"] > 50000
    assert provider.return_value.get_recycling_points.await_args.kwargs["radius"] > 50000
