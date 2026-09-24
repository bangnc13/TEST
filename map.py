import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
from geopy.distance import geodesic

st.set_page_config(layout="wide", page_title="Xác định vị trí đứt cáp")

st.title("📍 Hệ thống xác định vị trí đứt cáp trên bản đồ")

# 1. Sidebar - Upload các file dữ liệu
st.sidebar.header("📂 Tải lên dữ liệu")
file_geojson = st.sidebar.file_uploader("Upload data.geojson (Tập điểm)", type=["geojson", "json"])
file_excel = st.sidebar.file_uploader("Upload Data(1).xlsx (Uplink & Đoạn cáp)", type=["xlsx"])
file_kml_cot = st.sidebar.file_uploader("Upload KML Cột điện", type=["kml"])
file_kml_tuyen = st.sidebar.file_uploader("Upload duong_day.kml", type=["kml"])

@st.cache_data
def load_data(geojson_file, excel_file):
    # Đọc tập điểm GeoJSON
    gdf_tap_diem = gpd.read_file(geojson_file)
    
    # Đọc Excel
    df_uplink = pd.read_excel(excel_file, sheet_name="Uplink")
    df_doan_cap = pd.read_excel(excel_file, sheet_name="Đoạn cáp")
    
    return gdf_tap_diem, df_uplink, df_doan_cap

def calculate_break_point(start_coords, end_coords, ratio):
    """Nội suy tọa độ điểm đứt dựa trên tỷ lệ cách điểm đầu"""
    lat1, lon1 = start_coords
    lat2, lon2 = end_coords
    
    lat_break = lat1 + ratio * (lat2 - lat1)
    lon_break = lon1 + ratio * (lon2 - lon1)
    return lat_break, lon_break

if file_geojson and file_excel:
    gdf_tap_diem, df_uplink, df_doan_cap = load_data(file_geojson, file_excel)
    
    st.sidebar.success("Tải dữ liệu thành công!")
    
    # Form nhập thông tin sự cố
    st.sidebar.header("🔍 Nhập thông tin đo đứt cáp")
    
    # Lấy danh sách danh mục tập điểm
    list_tap_diem = gdf_tap_diem['name'].dropna().unique().tolist() if 'name' in gdf_tap_diem.columns else []
    
    diem_do = st.sidebar.selectbox("Chọn điểm đo (TQGP0XX.0XXX/HX):", list_tap_diem)
    khoang_cach_do = st.sidebar.number_input("Khoảng cách đo được (m):", min_value=0.0, value=150.0, step=10.0)
    
    if st.sidebar.button("Tìm vị trí đứt"):
        # Lấy tọa độ điểm bắt đầu từ GeoJSON
        start_point_row = gdf_tap_diem[gdf_tap_diem['name'] == diem_do]
        
        if not start_point_row.empty:
            start_geom = start_point_row.geometry.values[0]
            start_lon, start_lat = start_geom.x, start_geom.y
            
            # Tra cứu hướng Uplink từ điểm đo đến HO
            row_uplink = df_uplink[df_uplink['DiemDau'] == diem_do]
            
            if not row_uplink.empty:
                diem_tiep_theo = row_uplink['DiemCuoi'].values[0]
                
                # Lấy tọa độ điểm tiếp theo
                next_point_row = gdf_tap_diem[gdf_tap_diem['name'] == diem_tiep_theo]
                
                if not next_point_row.empty:
                    next_geom = next_point_row.geometry.values[0]
                    next_lon, next_lat = next_geom.x, next_geom.y
                    
                    # Lấy chiều dài đoạn cáp từ Excel
                    row_cap = df_doan_cap[
                        ((df_doan_cap['DiemA'] == diem_do) & (df_doan_cap['DiemB'] == diem_tiep_theo)) |
                        ((df_doan_cap['DiemB'] == diem_do) & (df_doan_cap['DiemA'] == diem_tiep_theo))
                    ]
                    
                    chieu_dai_doan = row_cap['DoDai'].values[0] if not row_cap.empty else 500.0
                    
                    # Tính tỷ lệ vị trí đứt trên đoạn cáp
                    if khoang_cach_do <= chieu_dai_doan:
                        ratio = khoang_cach_do / chieu_dai_doan
                        break_lat, break_lon = calculate_break_point((start_lat, start_lon), (next_lat, next_lon), ratio)
                        
                        st.success(f"Tọa độ điểm đứt ước tính: **{break_lat:.6f}, {break_lon:.6f}**")
                        
                        # Hiển thị Bản đồ
                        m = folium.Map(location=[start_lat, start_lon], zoom_start=16)
                        
                        # Marker điểm đo
                        folium.Marker(
                            [start_lat, start_lon], 
                            popup=f"Điểm đo: {diem_do}", 
                            icon=folium.Icon(color='blue', icon='info-sign')
                        ).add_to(m)
                        
                        # Marker điểm tiếp theo
                        folium.Marker(
                            [next_lat, next_lon], 
                            popup=f"Hướng đến: {diem_tiep_theo}", 
                            icon=folium.Icon(color='green')
                        ).add_to(m)
                        
                        # Marker điểm đứt
                        folium.Marker(
                            [break_lat, break_lon], 
                            popup=f"💥 ĐIỂM ĐỨT CÁP ({khoang_cach_do}m)", 
                            icon=folium.Icon(color='red', icon='remove')
                        ).add_to(m)
                        
                        # Vẽ đường cáp nối
                        folium.PolyLine([(start_lat, start_lon), (next_lat, next_lon)], color="gray", weight=3, opacity=0.7).add_to(m)
                        folium.PolyLine([(start_lat, start_lon), (break_lat, break_lon)], color="red", weight=5, opacity=0.9).add_to(m)
                        
                        st_folium(m, width=1000, height=600)
                    else:
                        st.warning("Khoảng cách đo vượt quá chiều dài của đoạn cáp hiện tại. Cần duyệt thêm qua tập điểm tiếp theo.")
                else:
                    st.error(f"Không tìm thấy tọa độ cho tập điểm tiếp theo: {diem_tiep_theo}")
            else:
                st.error("Không tìm thấy đường Uplink cho điểm đo này.")
        else:
            st.error("Không tìm thấy điểm đo trong file data.geojson.")
else:
    st.info("Vui lòng tải lên file GeoJSON và Excel để bắt đầu.")