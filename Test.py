import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý Mạng lưới Cáp")

# Khởi tạo Geod WGS84 để tính khoảng cách trái đất chính xác theo mét
geod = Geod(ellps="WGS84")

# ==========================================
# 1. HÀM CHUẨN HÓA TÊN TẬP ĐIỂM
# ==========================================
def clean_node_name(raw_name):
    if not raw_name or pd.isna(raw_name):
        return ""
    
    name = str(raw_name).strip()
    # Loại bỏ phần đuôi cổng thiết bị (/16, /6, /4, /5...)
    cleaned = re.sub(r'/(CO|HO|MO|CAP|P|D)/\d+$', r'/\1', name, flags=re.IGNORECASE)
    if cleaned == name:
        cleaned = re.sub(r'/\d+$', '', name)
    return cleaned

# ==========================================
# 2. HÀM NẠP DỮ LIỆU
# ==========================================
@st.cache_data
def load_data(geojson_path, excel_path):
    if not os.path.exists(geojson_path) or not os.path.exists(excel_path):
        return None, None, None, "Không tìm thấy file data.geojson hoặc Data.xlsx!"

    try:
        gdf_points = gpd.read_file(geojson_path)
        excel_file = pd.ExcelFile(excel_path)
        sheet_names = excel_file.sheet_names
        sheet_map = {str(s).strip().upper(): s for s in sheet_names}
        
        uplink_sheet = sheet_map.get('UPLINK', sheet_names[0])
        df_uplink = pd.read_excel(excel_path, sheet_name=uplink_sheet)
        
        df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC']) if 'DC' in sheet_map else pd.DataFrame()

        return gdf_points, df_uplink, df_dc, None
    except Exception as e:
        return None, None, None, f"Lỗi đọc file: {e}"

# ==========================================
# 3. BÓC TÁCH TUYẾN CHUỖI UPLINK
# ==========================================
def extract_route_from_uplink(df_uplink, start_node, end_node):
    s_clean = clean_node_name(start_node)
    e_clean = clean_node_name(end_node)
    
    uplink_col = None
    for col in df_uplink.columns:
        if 'UPLINK' in str(col).upper() or 'THÔNG SỐ' in str(col).upper():
            uplink_col = col
            break
    if uplink_col is None and len(df_uplink.columns) >= 2:
        uplink_col = df_uplink.columns[1]

    if not uplink_col:
        return None, "Không tìm thấy cột thông số Uplink trong file Excel!"

    for _, row in df_uplink.iterrows():
        route_str = str(row[uplink_col]) if pd.notna(row[uplink_col]) else ""
        if '=>' in route_str:
            raw_nodes = route_str.split('=>')
            clean_nodes = [clean_node_name(n) for n in raw_nodes if 'Trung Gian' not in str(n)]
            
            final_nodes = []
            for n in clean_nodes:
                if n and (not final_nodes or final_nodes[-1] != n):
                    final_nodes.append(n)

            if s_clean in final_nodes and e_clean in final_nodes:
                idx_s = final_nodes.index(s_clean)
                idx_e = final_nodes.index(e_clean)
                
                if idx_s <= idx_e:
                    sub_route = final_nodes[idx_s : idx_e + 1]
                else:
                    sub_route = final_nodes[idx_e : idx_s + 1][::-1]
                    
                return sub_route, None

    return None, f"Không tìm thấy dòng Uplink chứa cả 2 tập điểm {start_node} và {end_node}!"

# ==========================================
# 4. HÀM TÍNH TỌA ĐỘ ĐIỂM ĐO TRÊN ĐƯỜNG CÁP
# ==========================================
def find_point_along_path(coords, target_dist):
    """
    Duyệt qua danh sách tọa độ coords [(lat, lon), ...]
    Tính khoảng cách dồn, trả về:
    - path_measured: Các tọa độ từ Điểm Gốc -> Điểm đo
    - path_remaining: Các tọa độ từ Điểm đo -> Điểm Định hướng
    - target_coord: Tọa độ chính xác điểm đo (lat, lon)
    - total_len: Tổng chiều dài toàn bộ tuyến cáp
    """
    accumulated = 0.0
    path_measured = [coords[0]]
    target_coord = None
    target_index = -1
    
    # Tính tổng chiều dài toàn tuyến
    total_len = 0.0
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i+1]
        _, _, seg_dist = geod.inv(p1[1], p1[0], p2[1], p2[0])
        total_len += seg_dist

    # Tìm vị trí target_dist
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i+1]
        
        # geod.inv nhận (lon, lat)
        az12, az21, seg_dist = geod.inv(p1[1], p1[0], p2[1], p2[0])
        
        if accumulated + seg_dist >= target_dist:
            # Điểm cần tìm nằm trên phân đoạn p1 -> p2
            remain = target_dist - accumulated
            target_lon, target_lat, _ = geod.fwd(p1[1], p1[0], az12, remain)
            target_coord = (target_lat, target_lon)
            path_measured.append(target_coord)
            
            # Phần còn lại của đường cáp
            path_remaining = [target_coord] + coords[i+1:]
            return path_measured, path_remaining, target_coord, total_len
        else:
            accumulated += seg_dist
            path_measured.append(p2)
            
    # Nếu khoảng cách đo lớn hơn tổng chiều dài tuyến cáp
    target_coord = coords[-1]
    return coords, [coords[-1]], target_coord, total_len

# ==========================================
# 5. GIAO DIỆN STREAMLIT CHÍNH
# ==========================================
st.title("📍 Hệ thống Tính toán & Định vị Vị trí Sự cố Cáp")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

gdf_pts, df_uplink, df_dc, err = load_data(geojson_file, excel_file)

if err:
    st.error(err)
else:
    name_col_json = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    
    # Tạo từ điển Tên Tập Điểm -> Tọa độ (y, x)
    coord_dict = {}
    for _, row in gdf_pts.iterrows():
        if pd.notna(row[name_col_json]):
            node_name = str(row[name_col_json]).strip()
            coord_dict[node_name] = (row.geometry.y, row.geometry.x)

    raw_points = gdf_pts[name_col_json].dropna().astype(str).tolist()
    list_points = sorted(list(set([p.strip() for p in raw_points if p.strip()])))

    # Sidebar
    st.sidebar.header("Thông tin đo đạc OTDR")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach_input = st.sidebar.number_input("Khoảng cách đo trên cáp (m):", min_value=0.0, value=500.0, step=10.0)
    btn_calc = st.sidebar.button("Tính toán & Định vị trên cáp")

    # Bản đồ mặc định
    m = folium.Map(
        location=[gdf_pts.geometry.y.mean(), gdf_pts.geometry.x.mean()], 
        zoom_start=15, 
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
        attr='Google Satellite'
    )

    if btn_calc and td_do and td_huong:
        route_nodes, route_err = extract_route_from_uplink(df_uplink, td_do, td_huong)

        if route_err:
            st.error(route_err)
        else:
            # Lấy tọa độ các điểm thuộc route
            route_coords = []
            valid_nodes = []
            
            for node in route_nodes:
                if node in coord_dict:
                    route_coords.append(coord_dict[node])
                    valid_nodes.append(node)

            if len(route_coords) < 2:
                st.error("Không đủ tọa độ để tạo tuyến cáp nối giữa 2 điểm này!")
            else:
                # Tính vị trí chính xác của điểm đo trên tuyến cáp
                path_meas, path_rem, target_coord, total_len = find_point_along_path(route_coords, khoang_cach_input)
                
                st.success(f"📌 Tổng chiều dài tuyến cáp: **{total_len:.1f} m** | Khoảng cách đo: **{khoang_cach_input:.1f} m**")

                # 1. Vẽ CÁC TẬP ĐIỂM trên tuyến
                for node in valid_nodes:
                    pos = coord_dict[node]
                    icon_color = "green" if node == clean_node_name(td_do) else ("red" if node == clean_node_name(td_huong) else "blue")

                    folium.CircleMarker(
                        location=pos, radius=5, color=icon_color, fill=True, fill_color=icon_color, fill_opacity=1.0, tooltip=node
                    ).add_to(m)

                    folium.Marker(
                        location=pos,
                        icon=folium.DivIcon(
                            html=f'<div style="font-size: 9pt; color: #00FFFF; font-weight: bold; text-shadow: 1px 1px 2px black; white-space: nowrap;">{node}</div>',
                            icon_anchor=(-8, 10)
                        )
                    ).add_to(m)

                # 2. Vẽ ĐOẠN CÁP ĐÃ ĐO (Từ Gốc -> Điểm đo): Màu đỏ đậm, nét dày
                folium.PolyLine(
                    path_meas, color="#FF0000", weight=6, opacity=0.9, tooltip=f"Đoạn cáp đã đo ({khoang_cach_input}m)"
                ).add_to(m)

                # 3. Vẽ ĐOẠN CÁP CÒN LẠI (Từ Điểm đo -> Định hướng): Màu xanh lơ nét đứt
                if len(path_rem) >= 2:
                    folium.PolyLine(
                        path_rem, color="#00FFFF", weight=4, opacity=0.8, dash_array='6, 8', tooltip="Đoạn cáp còn lại"
                    ).add_to(m)

                # 4. ĐÁNH DẤU CHÍNH XÁC ĐIỂM ĐO / ĐIỂM SỰ CỐ TRÊN ĐƯỜNG CÁP
                folium.Marker(
                    target_coord,
                    popup=f"Vị trí đo: {khoang_cach_input}m trên tuyến cáp",
                    icon=folium.Icon(color="orange", icon="warning-sign")
                ).add_to(m)

                # Nhãn giá trị khoảng cách ngay tại điểm tìm được
                folium.Marker(
                    target_coord,
                    icon=folium.DivIcon(
                        html=f'<div style="font-size: 11pt; color: yellow; font-weight: bold; background-color: rgba(255,0,0,0.9); padding: 3px 8px; border-radius: 4px; border: 1px solid white; white-space: nowrap;">📍 Vị trí {khoang_cach_input}m</div>',
                        icon_anchor=(-15, -10)
                    )
                ).add_to(m)

                # Tự động di chuyển góc nhìn bản đồ đến Điểm đo
                m.location = target_coord
                m.zoom_start = 17

    else:
        for name, pos in coord_dict.items():
            folium.CircleMarker(
                location=pos, radius=3, color="gray", fill=True, fill_color="gray", fill_opacity=0.5, tooltip=name
            ).add_to(m)

    folium_static(m, width=1100, height=650)
