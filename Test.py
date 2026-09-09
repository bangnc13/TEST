import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
from pyproj import Geod
from shapely.geometry import Point, LineString, MultiLineString
from shapely.ops import substring
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý & Mô phỏng Tuyến Cáp")

# Trắc địa WGS84
geod = Geod(ellps="WGS84")

# ==========================================
# 1. HÀM CHUẨN HÓA MÃ TẬP ĐIỂM (CORE CODE)
# ==========================================
def extract_core_code(raw_name):
    """
    Rút gọn tên tập điểm về mã cốt lõi:
    'TQGP001.0198/HO' -> 'TQGP001.198'
    'TQGP001.0200/CO/2' -> 'TQGP001.200'
    """
    if not raw_name or pd.isna(raw_name):
        return ""
    
    s = str(raw_name).strip().upper()
    s_base = s.split('/')[0].strip()
    
    match = re.search(r'([A-Z]+\d+)\.(\d+)', s_base)
    if match:
        prefix = match.group(1)
        number = int(match.group(2))
        return f"{prefix}.{number}"
    
    return s_base

# ==========================================
# 2. NẠP DỮ LIỆU EXCEL & GEOJSON
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
# 3. TRÍCH XUẤT ĐOẠN CÁP UỐN LƯỢN CHỈ TỪ GEOJSON
# ==========================================
def get_geojson_cable_segment(p1_coord, p2_coord, gdf_lines):
    """
    Chỉ tìm và cắt duy nhất đoạn LineString thuộc GeoJSON bao phủ giữa 2 điểm P1 và P2.
    Không dùng OSRM hay đường giao thông công cộng.
    """
    lat1, lon1 = p1_coord
    lat2, lon2 = p2_coord
    
    pt1 = Point(lon1, lat1)
    pt2 = Point(lon2, lat2)
    
    best_subline_coords = None
    min_dist_sum = float('inf')
    
    for _, row in gdf_lines.iterrows():
        geom = row.geometry
        if geom.is_empty:
            continue
            
        lines = list(geom.geoms) if geom.type == 'MultiLineString' else [geom]
        for line in lines:
            # Tính tổng khoảng cách từ 2 điểm tới đường cáp này
            d1 = line.distance(pt1)
            d2 = line.distance(pt2)
            d_total = d1 + d2
            
            if d_total < min_dist_sum:
                min_dist_sum = d_total
                
                # Chiếu 2 điểm lên đường cáp LineString
                proj1 = line.project(pt1)
                proj2 = line.project(pt2)
                
                start_p, end_p = min(proj1, proj2), max(proj1, proj2)
                sub_l = substring(line, start_p, end_p)
                
                if not sub_l.is_empty:
                    if sub_l.type == 'LineString':
                        coords = [(lat, lon) for lon, lat in sub_l.coords]
                    else:
                        coords = []
                        for g in sub_l.geoms:
                            coords.extend([(lat, lon) for lon, lat in g.coords])
                    
                    if len(coords) >= 2:
                        best_subline_coords = coords

    # Nếu tìm thấy đường cáp trong GeoJSON đủ gần
    if best_subline_coords:
        # Kiếm tra hướng đi từ P1 -> P2
        d_start = (best_subline_coords[0][0]-lat1)**2 + (best_subline_coords[0][1]-lon1)**2
        d_end = (best_subline_coords[-1][0]-lat1)**2 + (best_subline_coords[-1][1]-lon1)**2
        if d_end < d_start:
            best_subline_coords = best_subline_coords[::-1]
        return best_subline_coords

    # Nếu không tìm thấy LineString phù hợp, nối trực tiếp 2 điểm
    return [p1_coord, p2_coord]

def build_pure_geojson_path(route_nodes, valid_coords, gdf_lines):
    full_path = []
    for i in range(len(route_nodes) - 1):
        n1, n2 = route_nodes[i], route_nodes[i+1]
        p1 = valid_coords[n1]
        p2 = valid_coords[n2]
        
        segment = get_geojson_cable_segment(p1, p2, gdf_lines)
        
        if not full_path:
            full_path.extend(segment)
        else:
            full_path.extend(segment[1:])
    return full_path

# ==========================================
# 4. BÓC TÁCH CHUỖI UPLINK
# ==========================================
def extract_route_from_uplink(df_uplink, start_node, end_node):
    core_s = extract_core_code(start_node)
    core_e = extract_core_code(end_node)
    
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
            node_list = [n.strip() for n in raw_nodes if 'Trung Gian' not in str(n)]
            core_list = [extract_core_code(n) for n in node_list]

            if core_s in core_list and core_e in core_list:
                idx_s = core_list.index(core_s)
                idx_e = core_list.index(core_e)
                
                if idx_s <= idx_e:
                    sub_route = node_list[idx_s : idx_e + 1]
                else:
                    sub_route = node_list[idx_e : idx_s + 1][::-1]
                    
                return sub_route, None

    return None, f"Không tìm thấy dòng Uplink chứa cả 2 tập điểm {start_node} và {end_node}!"

# ==========================================
# 5. TÍNH KHOẢNG CÁCH DỒN & ĐỊNH VỊ ĐIỂM SỰ CỐ
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
st.title("📍 Hệ thống Quản lý & Mô phỏng Tuyến Cáp Thực Tế")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

gdf_pts, gdf_lines, df_uplink, df_dc, err = load_data(geojson_file, excel_file)

if err:
    st.error(err)
else:
    name_col_json = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    
    coord_dict = {}
    core_map = {}
    
    for _, row in gdf_pts.iterrows():
        if pd.notna(row[name_col_json]):
            node_name = str(row[name_col_json]).strip()
            coord = (row.geometry.y, row.geometry.x)
            
            coord_dict[node_name] = coord
            c_code = extract_core_code(node_name)
            if c_code:
                core_map[c_code] = coord

    raw_points = gdf_pts[name_col_json].dropna().astype(str).tolist()
    list_points = sorted(list(set([p.strip() for p in raw_points if p.strip()])))

    # Sidebar
    st.sidebar.header("Thông tin đo đạc OTDR")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach_input = st.sidebar.number_input("Khoảng cách đo trên cáp (m):", min_value=0.0, value=300.0, step=10.0)
    btn_calc = st.sidebar.button("Tính toán & Mô phỏng đường cáp")

    m = folium.Map(
        location=[gdf_pts.geometry.y.mean(), gdf_pts.geometry.x.mean()], 
        zoom_start=16, 
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
        attr='Google Satellite'
    )

    if btn_calc and td_do and td_huong:
        route_nodes, route_err = extract_route_from_uplink(df_uplink, td_do, td_huong)

        if route_err:
            st.error(route_err)
        else:
            valid_nodes = []
            valid_coords = {}
            for n in route_nodes:
                if n in coord_dict:
                    valid_nodes.append(n)
                    valid_coords[n] = coord_dict[n]
                else:
                    c_code = extract_core_code(n)
                    if c_code in core_map:
                        valid_nodes.append(n)
                        valid_coords[n] = core_map[c_code]

            if len(valid_nodes) < 2:
                st.error("Không đủ tọa độ tập điểm để vẽ tuyến!")
            else:
                # CHỈ LẤY ĐƯỜNG CÁP CHÍNH XÁC TỪ GEOJSON (KHÔNG DÙNG OSRM)
                full_curved_coords = build_pure_geojson_path(valid_nodes, valid_coords, gdf_lines)

                path_meas, path_rem, target_coord, total_len = find_point_along_path(full_curved_coords, khoang_cach_input)
                
                st.success(f"📌 Tổng chiều dài tuyến cáp GeoJSON thực tế: **{total_len:.1f} m** | Khoảng cách đo: **{khoang_cach_input:.1f} m**")

                # 1. Vẽ các Tập điểm
                for node in valid_nodes:
                    pos = valid_coords[node]
                    icon_color = "green" if extract_core_code(node) == extract_core_code(td_do) else ("red" if extract_core_code(node) == extract_core_code(td_huong) else "blue")

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

                # 2. Vẽ ĐOẠN CÁP ĐÃ ĐO (Màu đỏ)
                folium.PolyLine(
                    path_meas, color="#FF0000", weight=5, opacity=0.9, tooltip=f"Đoạn cáp đã đo ({khoang_cach_input}m)"
                ).add_to(m)

                # 3. Vẽ ĐOẠN CÁP CÒN LẠI (Màu xanh nét đứt)
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
