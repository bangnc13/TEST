import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import nearest_points, snap, split, substring
import networkx as nx
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Định vị & Quản lý Tuyến Cáp")

# Trắc địa WGS84
geod = Geod(ellps="WGS84")

# ==========================================
# 1. CHUẨN HÓA TÊN TẬP ĐIỂM
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
# 2. NẠP DỮ LIỆU & TÁCH GEOMETRY
# ==========================================
@st.cache_data
def load_data(geojson_path, excel_path):
    if not os.path.exists(geojson_path) or not os.path.exists(excel_path):
        return None, None, None, None, "Không tìm thấy file data.geojson hoặc Data.xlsx!"

    try:
        gdf_all = gpd.read_file(geojson_path)
        
        # Tách Point và LineString
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
# 3. BÓC TÁCH CHUỖI UPLINK
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
# 4. THUẬT TOÁN TÌM ĐƯỜNG UỐN LƯỢN TRONG CÁC LINESTRING
# ==========================================
def find_detailed_line_between_points(p1_coord, p2_coord, gdf_lines):
    """
    p1_coord, p2_coord: (lat, lon)
    Tìm LineString trong GeoJSON đi qua hoặc nối 2 điểm này.
    """
    pt1 = Point(p1_coord[1], p1_coord[0]) # lon, lat
    pt2 = Point(p2_coord[1], p2_coord[0])

    best_coords = None
    min_score = float('inf')

    for _, row in gdf_lines.iterrows():
        geom = row.geometry
        if geom.is_empty:
            continue

        lines = list(geom.geoms) if geom.type == 'MultiLineString' else [geom]

        for line in lines:
            d1 = line.distance(pt1)
            d2 = line.distance(pt2)

            # Đánh giá độ gần của LineString với cả 2 điểm
            score = d1 + d2
            if score < min_score:
                min_score = score
                
                # Chiếu 2 điểm lên LineString để lấy đúng đoạn giữa 2 điểm
                proj1 = line.project(pt1)
                proj2 = line.project(pt2)

                start_pr, end_pr = min(proj1, proj2), max(proj1, proj2)
                
                # Trích xuất đoạn cáp uốn lượn thực sự giữa 2 điểm
                sub_l = substring(line, start_pr, end_pr)
                
                if not sub_l.is_empty:
                    if sub_l.type == 'LineString':
                        coords = [(lat, lon) for lon, lat in sub_l.coords]
                    else:
                        coords = []
                        for g in sub_l.geoms:
                            coords.extend([(lat, lon) for lon, lat in g.coords])
                    
                    if len(coords) >= 2:
                        # Kiểm tra hướng đi p1 -> p2
                        d_start = (coords[0][0]-p1_coord[0])**2 + (coords[0][1]-p1_coord[1])**2
                        d_end = (coords[-1][0]-p1_coord[0])**2 + (coords[-1][1]-p1_coord[1])**2
                        if d_end < d_start:
                            coords = coords[::-1]
                        best_coords = coords

    # Nếu khoảng cách đến LineString nhỏ hơn ngưỡng cho phép (khoảng 150m)
    if min_score < 0.0015 and best_coords:
        return best_coords
    else:
        # Dự phòng nếu không có LineString tương ứng
        return [p1_coord, p2_coord]

def build_complete_detailed_path(route_coords, gdf_lines):
    full_path = []
    for i in range(len(route_coords) - 1):
        p1 = route_coords[i]
        p2 = route_coords[i+1]
        
        segment_path = find_detailed_line_between_points(p1, p2, gdf_lines)
        
        if not full_path:
            full_path.extend(segment_path)
        else:
            full_path.extend(segment_path[1:])
            
    return full_path

# ==========================================
# 5. TÍNH KHOẢNG CÁCH DỒN & ĐỊNH VỊ ĐIỂM ĐO
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
# 6. GIAO DIỆN CHÍNH STREAMLIT
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
                st.error("Không đủ tọa độ tập điểm để vẽ tuyến!")
            else:
                # XÂY DỰNG TUYẾN CÁP UỐN LƯỢN CHI TIẾT
                detailed_coords = build_complete_detailed_path(route_coords, gdf_lines)

                # Tính vị trí chính xác điểm đo
                path_meas, path_rem, target_coord, total_len = find_point_along_path(detailed_coords, khoang_cach_input)
                
                st.success(f"📌 Tổng chiều dài cáp uốn lượn thực tế: **{total_len:.1f} m** | Khoảng cách đo: **{khoang_cach_input:.1f} m**")

                # 1. Vẽ các Tập điểm trên tuyến
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

                # 2. Vẽ ĐOẠN CÁP ĐÃ ĐO (Đỏ uốn lượn thực tế)
                folium.PolyLine(
                    path_meas, color="#FF0000", weight=5, opacity=0.9, tooltip=f"Đoạn cáp đã đo ({khoang_cach_input}m)"
                ).add_to(m)

                # 3. Vẽ ĐOẠN CÁP CÒN LẠI (Xanh lơ uốn lượn nét đứt)
                if len(path_rem) >= 2:
                    folium.PolyLine(
                        path_rem, color="#00FFFF", weight=4, opacity=0.8, dash_array='6, 8', tooltip="Đoạn cáp còn lại"
                    ).add_to(m)

                # 4. VỊ TRÍ ĐIỂM ĐO / ĐIỂM SỰ CỐ
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
