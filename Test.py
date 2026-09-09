import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod
from shapely.geometry import Point, LineString, MultiLineString
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý & Định vị Tuyến Cáp")

# Khởi tạo Geod WGS84
geod = Geod(ellps="WGS84")

# ==========================================
# 1. HÀM CHUẨN HÓA TÊN TẬP ĐIỂM
# ==========================================
def clean_node_name(raw_name):
    if not raw_name or pd.isna(raw_name):
        return ""
    name = str(raw_name).strip()
    cleaned = re.sub(r'/(CO|HO|MO|CAP|P|D)/\d+$', r'/\1', name, flags=re.IGNORECASE)
    if cleaned == name:
        cleaned = re.sub(r'/\d+$', '', name)
    return cleaned

# ==========================================
# 2. HÀM NẠP & TÁCH DỮ LIỆU GEOJSON
# ==========================================
@st.cache_data
def load_data(geojson_path, excel_path):
    if not os.path.exists(geojson_path) or not os.path.exists(excel_path):
        return None, None, None, None, "Không tìm thấy file data.geojson hoặc Data.xlsx!"

    try:
        gdf_all = gpd.read_file(geojson_path)
        
        # Tách riêng Point (Tập điểm) và LineString (Tuyến cáp thực tế)
        gdf_points = gdf_all[gdf_all.geometry.type == 'Point'].copy()
        gdf_lines = gdf_all[gdf_all.geometry.type.isin(['LineString', 'MultiLineString'])].copy()

        excel_file = pd.ExcelFile(excel_path)
        sheet_names = excel_file.sheet_names
        sheet_map = {str(s).strip().upper(): s for s in sheet_names}
        
        uplink_sheet = sheet_map.get('UPLINK', sheet_names[0])
        df_uplink = pd.read_excel(excel_path, sheet_name=uplink_sheet)
        df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC']) if 'DC' in sheet_map else pd.DataFrame()

        return gdf_points, gdf_lines, df_uplink, df_dc, None
    except Exception as e:
        return None, None, None, None, f"Lỗi đọc file: {e}"

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
# 4. TRẮC ĐỊA & LẤY TỌA ĐỘ UỐN LƯỢN CHI TIẾT
# ==========================================
def get_detailed_segment_coords(p1_coord, p2_coord, gdf_lines):
    """
    Tìm đường LineString thực tế trong GeoJSON nối giữa 2 điểm p1 và p2.
    Nếu tìm thấy LineString uốn lượn, trả về danh sách toàn bộ tọa độ uốn lượn.
    Nếu không tìm thấy, trả về đoạn thẳng [p1, p2].
    """
    p1_pt = Point(p1_coord[1], p1_coord[0]) # lon, lat
    p2_pt = Point(p2_coord[1], p2_coord[0])
    
    # Tìm đường LineString tiếp xúc gần nhất với p1 và p2
    best_line = None
    min_dist_sum = float('inf')

    for _, row in gdf_lines.iterrows():
        geom = row.geometry
        if geom.type == 'MultiLineString':
            lines = list(geom.geoms)
        else:
            lines = [geom]

        for line in lines:
            d1 = line.distance(p1_pt)
            d2 = line.distance(p2_pt)
            # Ngưỡng chấp nhận kết nối (khoảng 50m trong độ)
            if d1 < 0.0005 and d2 < 0.0005:
                dist_sum = d1 + d2
                if dist_sum < min_dist_sum:
                    min_dist_sum = dist_sum
                    best_line = line

    if best_line is not None:
        coords = [(lat, lon) for lon, lat in best_line.coords]
        # Kiểm tra chiều đường cáp (từ p1 -> p2 hay ngược lại)
        d_start_p1 = (coords[0][0]-p1_coord[0])**2 + (coords[0][1]-p1_coord[1])**2
        d_end_p1 = (coords[-1][0]-p1_coord[0])**2 + (coords[-1][1]-p1_coord[1])**2
        
        if d_end_p1 < d_start_p1:
            coords = coords[::-1]
        return coords
    else:
        return [p1_coord, p2_coord]

def build_full_detailed_route(route_coords, gdf_lines):
    full_coords = []
    for i in range(len(route_coords) - 1):
        p1 = route_coords[i]
        p2 = route_coords[i+1]
        seg_coords = get_detailed_segment_coords(p1, p2, gdf_lines)
        
        if not full_coords:
            full_coords.extend(seg_coords)
        else:
            full_coords.extend(seg_coords[1:])
            
    return full_coords

# ==========================================
# 5. TÍNH VỊ TRÍ ĐIỂM ĐO TRÊN ĐƯỜNG UỐN LƯỢN
# ==========================================
def find_point_along_path(coords, target_dist):
    accumulated = 0.0
    path_measured = [coords[0]]
    
    total_len = 0.0
    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i+1]
        _, _, seg_dist = geod.inv(p1[1], p1[0], p2[1], p2[0])
        total_len += seg_dist

    for i in range(len(coords) - 1):
        p1 = coords[i]
        p2 = coords[i+1]
        
        az12, az21, seg_dist = geod.inv(p1[1], p1[0], p2[1], p2[0])
        
        if accumulated + seg_dist >= target_dist:
            remain = target_dist - accumulated
            target_lon, target_lat, _ = geod.fwd(p1[1], p1[0], az12, remain)
            target_coord = (target_lat, target_lon)
            path_measured.append(target_coord)
            
            path_remaining = [target_coord] + coords[i+1:]
            return path_measured, path_remaining, target_coord, total_len
        else:
            accumulated += seg_dist
            path_measured.append(p2)
            
    target_coord = coords[-1]
    return coords, [coords[-1]], target_coord, total_len

# ==========================================
# 6. GIAO DIỆN STREAMLIT CHÍNH
# ==========================================
st.title("📍 Hệ thống Quản lý & Định vị Vị trí Sự cố Cáp")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

gdf_pts, gdf_lines, df_uplink, df_dc, err = load_data(geojson_file, excel_file)

if err:
    st.error(err)
else:
    name_col_json = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    
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
            route_coords = []
            valid_nodes = []
            
            for node in route_nodes:
                if node in coord_dict:
                    route_coords.append(coord_dict[node])
                    valid_nodes.append(node)

            if len(route_coords) < 2:
                st.error("Không đủ tọa độ để tạo tuyến cáp!")
            else:
                # XÂY DỰNG TUYẾN CÁP UỐN LƯỢN CHI TIẾT TỪ GEOJSON
                detailed_coords = build_full_detailed_route(route_coords, gdf_lines)

                # Tính vị trí chính xác điểm đo trên đường uốn lượn
                path_meas, path_rem, target_coord, total_len = find_point_along_path(detailed_coords, khoang_cach_input)
                
                st.success(f"📌 Tổng chiều dài cáp uốn lượn thực tế: **{total_len:.1f} m** | Khoảng cách đo: **{khoang_cach_input:.1f} m**")

                # 1. Hiển thị các Tập điểm
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

                # 2. Vẽ ĐOẠN CÁP ĐÃ ĐO (Đỏ uốn lượn theo đường cáp thực tế)
                folium.PolyLine(
                    path_meas, color="#FF0000", weight=5, opacity=0.9, tooltip=f"Đoạn cáp đã đo ({khoang_cach_input}m)"
                ).add_to(m)

                # 3. Vẽ ĐOẠN CÁP CÒN LẠI (Xanh lơ uốn lượn nét đứt)
                if len(path_rem) >= 2:
                    folium.PolyLine(
                        path_rem, color="#00FFFF", weight=4, opacity=0.8, dash_array='6, 8', tooltip="Đoạn cáp còn lại"
                    ).add_to(m)

                # 4. ĐÁNH DẤU CHÍNH XÁC VỊ TRÍ ĐO
                folium.Marker(
                    target_coord,
                    popup=f"Vị trí đo: {khoang_cach_input}m",
                    icon=folium.Icon(color="orange", icon="warning-sign")
                ).add_to(m)

                folium.Marker(
                    target_coord,
                    icon=folium.DivIcon(
                        html=f'<div style="font-size: 11pt; color: yellow; font-weight: bold; background-color: rgba(255,0,0,0.9); padding: 3px 8px; border-radius: 4px; border: 1px solid white; white-space: nowrap;">📍 Vị trí {khoang_cach_input}m</div>',
                        icon_anchor=(-15, -10)
                    )
                ).add_to(m)

                m.fit_bounds(detailed_coords)

    else:
        for name, pos in coord_dict.items():
            folium.CircleMarker(
                location=pos, radius=3, color="gray", fill=True, fill_color="gray", fill_opacity=0.5, tooltip=name
            ).add_to(m)

    folium_static(m, width=1100, height=650)
