import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import linemerge, snap, substring
import networkx as nx
import requests
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý & Định vị Vị trí Sự cố Cáp")

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
# 2. NẠP DỮ LIỆU & BÓC TÁCH EXCEL & GEOJSON
# ==========================================
@st.cache_data
def load_data(geojson_path, excel_path):
    if not os.path.exists(geojson_path) or not os.path.exists(excel_path):
        return None, None, None, None, "Không tìm thấy file data.geojson hoặc Data.xlsx!"

    try:
        gdf_all = gpd.read_file(geojson_path)
        
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
# 3. LẤY CHIỀU DÀI THỰC TẾ TỪ SHEET DC / EXCEL
# ==========================================
def get_segment_length_from_dc(df_dc, node_a, node_b):
    if df_dc is None or df_dc.empty:
        return None
    
    clean_a = clean_node_name(node_a)
    clean_b = clean_node_name(node_b)
    
    for idx, row in df_dc.iterrows():
        row_str = " ".join([str(v) for v in row.values if pd.notna(v)])
        if clean_a in row_str and clean_b in row_str:
            for val in row.values:
                if isinstance(val, (int, float)) and 10 < val < 50000:
                    return float(val)
    return None

# ==========================================
# 4. THUẬT TOÁN BÁM ĐƯỜNG THỰC TẾ (OSRM & GRAPH ROUTING)
# ==========================================
def get_route_between_two_points(p1_coord, p2_coord, gdf_lines):
    """
    Tìm đường uốn lượn chính xác từ P1 -> P2.
    1. Tìm trong các đoạn LineString GeoJSON gần nhất
    2. Nếu GeoJSON bị đứt quãng -> Gọi OSRM Map Matching bám đường thực tế
    """
    lat1, lon1 = p1_coord
    lat2, lon2 = p2_coord
    
    pt1 = Point(lon1, lat1)
    pt2 = Point(lon2, lat2)
    
    # Cách A: Dùng Spatial Index & Nearest Line trong GeoJSON
    best_subline = None
    min_dist = float('inf')
    
    for _, row in gdf_lines.iterrows():
        geom = row.geometry
        if geom.is_empty:
            continue
        lines = list(geom.geoms) if geom.type == 'MultiLineString' else [geom]
        for line in lines:
            d = line.distance(pt1) + line.distance(pt2)
            if d < min_dist:
                min_dist = d
                proj1 = line.project(pt1)
                proj2 = line.project(pt2)
                start_p, end_p = min(proj1, proj2), max(proj1, proj2)
                sub_l = substring(line, start_p, end_p)
                if not sub_l.is_empty and sub_l.length > 0:
                    best_subline = sub_l

    # Sửa lỗi: dùng 'and' thay vì 'và'
    if min_dist < 0.001 and best_subline:
        coords = [(lat, lon) for lon, lat in best_subline.coords]
        d_start = (coords[0][0]-lat1)**2 + (coords[0][0]-lon1)**2
        d_end = (coords[-1][0]-lat1)**2 + (coords[-1][1]-lon1)**2
        if d_end < d_start:
            coords = coords[::-1]
        return coords

    # Cách B: Nếu GeoJSON không chứa đoạn nối liền, dùng OSRM Routing bám đường thực tế
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if 'routes' in data and len(data['routes']) > 0:
                osrm_coords = data['routes'][0]['geometry']['coordinates']
                return [(lat, lon) for lon, lat in osrm_coords]
    except Exception:
        pass

    # Dự phòng cuối cùng: nối trực tiếp P1 -> P2
    return [p1_coord, p2_coord]

def build_full_curved_path(route_nodes, coord_dict, gdf_lines):
    full_path = []
    for i in range(len(route_nodes) - 1):
        n1, n2 = route_nodes[i], route_nodes[i+1]
        if n1 in coord_dict and n2 in coord_dict:
            p1 = coord_dict[n1]
            p2 = coord_dict[n2]
            
            segment = get_route_between_two_points(p1, p2, gdf_lines)
            
            if not full_path:
                full_path.extend(segment)
            else:
                full_path.extend(segment[1:])
    return full_path

# ==========================================
# 5. BÓC TÁCH CHUỖI UPLINK
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
# 6. TÍNH KHOẢNG CÁCH DỒN VÀ TÍNH ĐIỂM SỰ CỐ
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
# 7. GIAO DIỆN CHÍNH STREAMLIT
# ==========================================
st.title("📍 Hệ thống Quản lý & Mô phỏng Tuyến Cáp Thực Tế")

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
    btn_calc = st.sidebar.button("Tính toán & Mô phỏng đường cáp")

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
            valid_nodes = [n for n in route_nodes if n in coord_dict]

            if len(valid_nodes) < 2:
                st.error("Không đủ tọa độ tập điểm để vẽ tuyến!")
            else:
                full_curved_coords = build_full_curved_path(valid_nodes, coord_dict, gdf_lines)

                dc_length = get_segment_length_from_dc(df_dc, td_do, td_huong)

                path_meas, path_rem, target_coord, total_len = find_point_along_path(full_curved_coords, khoang_cach_input)
                
                info_msg = f"📌 Tổng chiều dài tuyến cáp mô phỏng thực tế: **{total_len:.1f} m** | Khoảng cách đo: **{khoang_cach_input:.1f} m**"
                if dc_length:
                    info_msg += f" (Độ dài khai báo trong Sheet DC: **{dc_length:.1f} m**)"
                st.success(info_msg)

                # 1. Vẽ các Tập điểm trên tuyến
                for node in valid_nodes:
                    pos = coord_dict[node]
                    icon_color = "green" if node == clean_node_name(td_do) else ("red" if node == clean_node_name(td_huong) else "blue")

                    folium.CircleMarker(
                        location=pos, radius=6, color=icon_color, fill=True, fill_color=icon_color, fill_opacity=1.0, tooltip=node
                    ).add_to(m)

                    folium.Marker(
                        location=pos,
                        icon=folium.DivIcon(
                            html=f'<div style="font-size: 9pt; color: #00FFFF; font-weight: bold; text-shadow: 1px 1px 2px black; white-space: nowrap;">{node}</div>',
                            icon_anchor=(-8, 10)
                        )
                    ).add_to(m)

                # 2. Vẽ ĐOẠN CÁP ĐÃ ĐO (Đỏ uốn lượn chính xác)
                folium.PolyLine(
                    path_meas, color="#FF0000", weight=5, opacity=0.9, tooltip=f"Đoạn cáp đã đo ({khoang_cach_input}m)"
                ).add_to(m)

                # 3. Vẽ ĐOẠN CÁP CÒN LẠI (Xanh lơ uốn lượn nét đứt)
                if len(path_rem) >= 2:
                    folium.PolyLine(
                        path_rem, color="#00FFFF", weight=4, opacity=0.8, dash_array='6, 8', tooltip="Đoạn cáp còn lại"
                    ).add_to(m)

                # 4. VỊ TRÍ ĐIỂM SỰ CỐ / ĐIỂM ĐO
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

                m.fit_bounds(full_curved_coords)

    else:
        for name, pos in coord_dict.items():
            folium.CircleMarker(
                location=pos, radius=3, color="gray", fill=True, fill_color="gray", fill_opacity=0.5, tooltip=name
            ).add_to(m)

    folium_static(m, width=1100, height=650)
