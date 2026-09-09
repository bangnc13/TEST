import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import st_folium
from pyproj import Geod

st.set_page_config(layout="wide", page_title="Quản lý Mạng lưới Tập điểm")

# 1. HÀM NẠP DỮ LIỆU
@st.cache_data
def load_data(geojson_path, excel_path):
    gdf_points = gpd.read_file(geojson_path)
    excel_file = pd.ExcelFile(excel_path)
    sheet_names = excel_file.sheet_names
    sheet_map = {s.strip().upper(): s for s in sheet_names}
    
    df_uplink = pd.read_excel(excel_path, sheet_name=sheet_map.get('UPLINK', 0))
    df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC']) if 'DC' in sheet_map else pd.DataFrame()
    
    return gdf_points, df_uplink, df_dc

# 2. HÀM TÍNH TỌA ĐỘ
def calculate_new_point(gdf_points, start_name, dir_name, distance_m):
    pt_start = gdf_points[gdf_points['name'] == start_name]
    pt_dir = gdf_points[gdf_points['name'] == dir_name]
    
    if pt_start.empty or pt_dir.empty:
        return None, None, "Không tìm thấy tên tập điểm!"
        
    lon1, lat1 = pt_start.geometry.iloc[0].x, pt_start.geometry.iloc[0].y
    lon2, lat2 = pt_dir.geometry.iloc[0].x, pt_dir.geometry.iloc[0].y
    
    geod = Geod(ellps='WGS84')
    azimuth12, _, _ = geod.inv(lon1, lat1, lon2, lat2)
    end_lon, end_lat, _ = geod.fwd(lon1, lat1, azimuth12, distance_m)
    
    return (end_lat, end_lon), (lat1, lon1), None

# 3. GIAO DIỆN STREAMLIT
st.title("📍 Hệ thống Tính toán & Hiển thị Mạng lưới Tập điểm")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

try:
    gdf_pts, df_uplink, df_dc = load_data(geojson_file, excel_file)
    list_points = gdf_pts['name'].tolist() if 'name' in gdf_pts.columns else []

    # Thanh công cụ nhập liệu bên trái
    st.sidebar.header("Thông tin đo đạc")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach = st.sidebar.number_input("Khoảng cách đo (m):", min_value=0.0, value=10.0, step=1.0)
    
    btn_calc = st.sidebar.button("Tính toán & Vẽ bản đồ")

    # Trung tâm bản đồ
    center_lat = gdf_pts.geometry.y.mean()
    center_lon = gdf_pts.geometry.x.mean()
    m = folium.Map(location=[center_lat, center_lon], zoom_start=15)

    # Vẽ mạng lưới từ sheet Uplink
    coord_dict = {row['name']: (row.geometry.y, row.geometry.x) for _, row in gdf_pts.iterrows() if 'name' in row}
    for _, row in df_uplink.iterrows():
        # Điều chỉnh đúng tên 2 cột điểm nối trong file excel của bạn ở đây
        p1, p2 = row.iloc[0], row.iloc[1] 
        if p1 in coord_dict and p2 in coord_dict:
            folium.PolyLine([coord_dict[p1], coord_dict[p2]], color="blue", weight=2, opacity=0.6).add_to(m)

    # Vẽ các tập điểm
    for name, coord in coord_dict.items():
        folium.CircleMarker(location=coord, radius=3, color="black", fill=True, popup=name).add_to(m)

    # Xử lý tính toán khi nhấn nút
    if btn_calc and td_do and td_huong:
        target, start, err = calculate_new_point(gdf_pts, td_do, td_huong, khoang_cach)
        if err:
            st.error(err)
        else:
            st.success(f"Tọa độ mới: Lat {target[0]:.7f}, Lon {target[1]:.7f}")
            folium.Marker(start, popup=f"Gốc: {td_do}", icon=folium.Icon(color="green")).add_to(m)
            folium.Marker(target, popup="Điểm mới", icon=folium.Icon(color="red", icon="star")).add_to(m)
            folium.PolyLine([start, target], color="red", weight=3, dash_array='5, 10').add_to(m)
            m.location = [target[0], target[1]]

    # Hiển thị bản đồ lên web
    st_folium(m, width="100%", height=600)

except Exception as e:
    st.error(f"Đã xảy ra lỗi khi nạp dữ liệu: {e}")
