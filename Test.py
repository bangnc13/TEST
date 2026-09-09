import geopandas as gpd
import pandas as pd
import folium
from pyproj import Geod
from shapely.geometry import Point, LineString

# ==========================================
# 1. ĐỌC VÀ CHUẨN BỊ DỮ LIỆU
# ==========================================
def load_data(geojson_path, excel_path):
    # Đọc tọa độ từ GeoJSON
    gdf_points = gpd.read_file(geojson_path)
    
    # Đọc các sheet từ file Excel Data.xlsx
    df_uplink = pd.read_excel(excel_path, sheet_name='uplink')
    df_dc = pd.read_excel(excel_path, sheet_name='DC')
    
    return gdf_points, df_uplink, df_dc

# ==========================================
# 2. HÀM TÍNH TỌA ĐỘ ĐIỂM MỚI THEO HƯỚNG VÀ KHỎANG CÁCH
# ==========================================
def calculate_new_point(gdf_points, start_name, dir_name, distance_m):
    """
    start_name: Tên Tập điểm đo (TĐ đo)
    dir_name: Tên Tập điểm định hướng
    distance_m: Khoảng cách từ TĐ đo đến điểm cần tìm (mét)
    """
    # Tìm tọa độ 2 điểm trong GeoJSON (giả định cột tên điểm là 'name')
    pt_start = gdf_points[gdf_points['name'] == start_name]
    pt_dir = gdf_points[gdf_points['name'] == dir_name]
    
    if pt_start.empty or pt_dir.empty:
        raise ValueError("Không tìm thấy tên tập điểm trong file GeoJSON!")
        
    lon1, lat1 = pt_start.geometry.iloc[0].x, pt_start.geometry.iloc[0].y
    lon2, lat2 = pt_dir.geometry.iloc[0].x, pt_dir.geometry.iloc[0].y
    
    # Sử dụng PyProj Geod (Chuẩn Ellipsoid WGS84) để tính toán chính xác mặt cầu
    geod = Geod(ellps='WGS84')
    
    # Tính góc định hướng (Azimuth) từ điểm đo tới điểm hướng
    azimuth12, azimuth21, _ = geod.inv(lon1, lat1, lon2, lat2)
    
    # Định vị điểm mới theo góc azimuth và khoảng cách
    end_lon, end_lat, _ = geod.fwd(lon1, lat1, azimuth12, distance_m)
    
    return (end_lat, end_lon), (lat1, lon1)

# ==========================================
# 3. HIỂN THỊ MẠNG LƯỚI & TÍCH HỢP LÊN BẢN ĐỒ INTERACTIVE
# ==========================================
def create_map(gdf_points, df_uplink, target_coord, start_coord, output_html="map.html"):
    # Khởi tạo bản đồ Folium tại vị trí điểm đo
    m = folium.Map(location=[start_coord[0], start_coord[1]], zoom_start=16)
    
    # Tạo dictionary tra cứu tọa độ nhanh theo name
    coord_dict = {row['name']: (row.geometry.y, row.geometry.x) for _, row in gdf_points.iterrows()}
    
    # A. Vẽ các đường liên kết (Network Links) dựa vào sheet Uplink
    for _, row in df_uplink.iterrows():
        p1, p2 = row['From_Node'], row['To_Node']  # Thay tên cột tương ứng trong sheet uplink
        if p1 in coord_dict and p2 in coord_dict:
            line_coords = [coord_dict[p1], coord_dict[p2]]
            folium.PolyLine(
                line_coords, 
                color="blue", 
                weight=2, 
                opacity=0.7,
                popup=f"Link: {p1} - {p2}"
            ).add_to(m)
            
    # B. Hiển thị tất cả các Tập điểm gốc từ GeoJSON
    for name, coord in coord_dict.items():
        folium.CircleMarker(
            location=coord,
            radius=4,
            color="black",
            fill=True,
            popup=f"TĐ: {name}"
        ).add_to(m)
        
    # C. Đánh dấu Điểm Đo gốc và Điểm Mới cần tìm
    folium.Marker(
        location=start_coord,
        popup="TĐ Đo (Gốc)",
        icon=folium.Icon(color="green", icon="play")
    ).add_to(m)
    
    folium.Marker(
        location=target_coord,
        popup=f"Điểm Mới Cần Tìm",
        icon=folium.Icon(color="red", icon="star")
    ).add_to(m)
    
    # Vẽ đoạn nối từ TĐ Đo đến Điểm Mới
    folium.PolyLine(
        [start_coord, target_coord], 
        color="red", 
        weight=3, 
        dash_array='5, 10',
        popup="Hướng & Khoảng cách đo"
    ).add_to(m)
    
    m.save(output_html)
    print(f"Bản đồ đã được xuất ra file: {output_html}")

# ==========================================
# 4. CHƯƠNG TRÌNH CHÍNH (MAIN FUNCTION)
# ==========================================
if __name__ == "__main__":
    # Đường dẫn file
    geojson_file = "data.geojson"
    excel_file = "Data.xlsx"
    
    # Nạp dữ liệu
    gdf_pts, df_uplink, df_dc = load_data(geojson_file, excel_file)
    
    # Nhập thông tin từ người dùng
    td_do = input("Nhập tên Tập Điểm Đo (vd: tqgp001.0001): ").strip()
    td_huong = input("Nhập tên Tập Điểm Định Hướng (vd: tqgp001.0002): ").strip()
    khoang_cach = float(input("Nhập khoảng cách đo (mét): "))
    
    # Tính toán
    new_lat_lon, start_lat_lon = calculate_new_point(gdf_pts, td_do, td_huong, khoang_cach)
    
    print(f"\n---> Tọa độ điểm cần tìm (WGS84): Latitude = {new_lat_lon[0]:.7f}, Longitude = {new_lat_lon[1]:.7f}")
    
    # Vẽ bản đồ
    create_map(gdf_pts, df_uplink, new_lat_lon, start_lat_lon)
