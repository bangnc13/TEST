import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod

st.set_page_config(layout="wide", page_title="Quản lý Mạng lưới Tập điểm")

# 1. HÀM NẠP DỮ LIỆU
@st.cache_data
def load_data(geojson_path, excel_path):
    gdf_points = gpd.read_file(geojson_path)
    excel_file = pd.ExcelFile(excel_path)
    sheet_names = excel_file.sheet_names
    sheet_map = {str(s).strip().upper(): s for s in sheet_names}
    
    df_uplink = pd.read_excel(excel_path, sheet_name=sheet_map.get('UPLINK', 0))
    df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC']) if 'DC' in sheet_map else pd.DataFrame()
    
    return gdf_points, df_uplink, df_dc

# 2. HÀM TÍNH TỌA ĐỘ
def calculate_new_point(gdf_points, start_name, dir_name, distance_m):
    # Tìm theo cột chứa tên điểm (thường là 'name' hoặc cột đầu tiên)
    name_col = 'name' if 'name' in gdf_points.columns else gdf_points.columns[0]
    
    pt_start = gdf_points[gdf_points[name_col] == start_name]
    pt_dir = gdf_points[gdf_points[name_col] == dir_name]
    
    if pt_start.empty or pt_dir.empty:
        return None, None, "Không tìm thấy tên tập điểm trong GeoJSON!"
        
    lon1, lat1 = pt_start.geometry.iloc[0].x, pt_start.geometry.iloc[0].y
    lon2, lat2 = pt_dir.geometry.iloc[0].x, pt_dir.geometry.iloc[0].y
    
    geod = Geod(ellps='WGS84')
    azimuth12, _, _ = geod.inv(lon1, lat1, lon2, lat2)
    end_lon, end_lat, _ = geod.fwd(lon1, lat1, azimuth12, distance_m)
    
    return (end_lat, end_lon), (lat1, lon1), None

# 3. GIAO DIỆN CHÍNH
st.title("📍 Hệ thống Tính toán & Hiển thị Mạng lưới Tập điểm")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

try:
    gdf_pts, df_uplink, df_dc = load_data(geojson_file, excel_file)
    
    # Lấy danh sách tên tập điểm
    name_col = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    list_points = gdf_pts[name_col].astype(str).tolist()

    # Sidebar nhập liệu
    st.sidebar.header("Thông tin đo đạc")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach = st.sidebar.number_input("Khoảng cách đo (m):", min_value=0.0, value=100.0, step=1.0)
    btn_calc = st.sidebar.button("Tính toán & Vẽ bản đồ")

    # Khởi tạo bản đồ với vị trí trung tâm
    center_lat = float(gdf_pts.geometry.y.mean())
    center_lon = float(gdf_pts.geometry.x.mean())
    m = folium.Map(location=[center_lat, center_lon], zoom_start=16)

    # 1. Vẽ các đường liên kết từ sheet Uplink
    coord_dict = {str(row[name_col]): (row.geometry.y, row.geometry.x) for _, row in gdf_pts.iterrows()}
    
    col1_uplink = df_uplink.columns[0]
    col2_uplink = df_uplink.columns[1]
    
    for _, row in df_uplink.iterrows():
        p1, p2 = str(row[col1_uplink]), str(row[col2_uplink])
        if p1 in coord_dict and p2 in coord_dict:
            folium.PolyLine([coord_dict[p1], coord_dict[p2]], color="blue", weight=2, opacity=0.7).add_to(m)

    # 2. Vẽ tất cả Tập điểm lên bản đồ
    for name, coord in coord_dict.items():
        folium.CircleMarker(
            location=coord, 
            radius=4, 
            color="black", 
            fill=True, 
            fill_opacity=0.8,
            popup=name
        ).add_to(m)

    # 3. Tính toán và vẽ kết quả khi bấm nút
    if btn_calc and td_do and td_huong:
        target, start, err = calculate_new_point(gdf_pts, td_do, td_huong, khoang_cach)
        if err:
            st.error(err)
        else:
            st.success(f"📌 Tọa độ điểm mới: **Latitude: {target[0]:.7f} | Longitude: {target[1]:.7f}**")
            
            # Marker điểm gốc
            folium.Marker(start, popup=f"Gốc: {td_do}", icon=folium.Icon(color="green", icon="play")).add_to(m)
            # Marker điểm mới
            folium.Marker(target, popup="Điểm mới", icon=folium.Icon(color="red", icon="star")).add_to(m)
            # Đường nối điểm đo
            folium.PolyLine([start, target], color="red", weight=3, dash_array='5, 10').add_to(m)
            
            m.location = [target[0], target[1]]

    # Hiển thị bản đồ
    folium_static(m, width=1100, height=650)

except Exception as e:
    st.error(f"Lỗi nạp dữ liệu: {e}")
