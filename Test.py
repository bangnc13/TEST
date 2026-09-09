import json
import re

# Đọc file JSON gốc
with open("TQGP1.JSON", "r", encoding="utf-8") as f:
    data = json.load(f)

items = data["responseResult"]["result"]["objectInfo"]
features = []

for item in items:
    lat_lng_str = item.get("latLng", "")
    
    # Trích xuất cặp số từ chuỗi dạng "(21.xxx,105.xxx)"
    match = re.search(r"\(\s*([\d\.]+)\s*,\s*([\d\.]+)\s*\)", lat_lng_str)
    
    if match:
        lat = float(match.group(1))
        lng = float(match.group(2))
        
        # GeoJSON dùng định dạng [Longitude, Latitude] (Kinh độ trước, Vĩ độ sau)
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [lng, lat]
            },
            "properties": {
                "id": item.get("id"),
                "name": item.get("name"),
                "objectType": item.get("objectType"),
                "totalPort": int(item.get("totalPort", 0)),
                "portFree": int(item.get("portFree", 0)),
                "portUsed": int(item.get("portUsed", 0)),
                "spliter": item.get("spliter"),
                "hasTapdiem": item.get("hasTapdiem"),
                "cabType": item.get("cabType")
            }
        }
        features.append(feature)

geojson_data = {
    "type": "FeatureCollection",
    "features": features
}

# Xuất ra file GeoJSON chuẩn
with open("output.geojson", "w", encoding="utf-8") as f:
    json.dump(geojson_data, f, ensure_ascii=False, indent=2)

print(f"Đã chuyển đổi thành công {len(features)} điểm vào file output.geojson!")
