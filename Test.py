import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium import plugins
from streamlit_folium import folium_static
from pyproj import Geod
import networkx as nx
import re
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý Mạng lưới Cáp")

# ==========================================
# 1. HÀM CHUẨN HÓA TÊN TẬP ĐIỂM
# ==========================================
def clean_node_name(raw_name):
    """
    Chuyển tên như 'TQGP001.0075/HO/16' hoặc 'TQGP001.0316/CO/6' 
    thành tên chuẩn gốc 'TQGP001.0075/HO' để khớp với GeoJSON
    """
    if not isinstance(raw_name, str):
        return ""
    
    name = raw_name.strip()
    # Loại bỏ phần /16, /6, /4... ở cuối tên nếu có
    cleaned = re.sub(r'/(CO|HO|MO|CAP|P|D)/\d+$', r'/\1', name, flags=re.IGNORECASE)
    if cleaned == name:
        # Nếu chưa cắt được, thử cắt đuôi số sau dấu / cuối cùng
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
        
        # Đọc sheet uplink
        uplink_sheet = sheet_map.get('UPLINK', sheet_names[0])
        df_uplink = pd.read_excel(excel_path, sheet_name=uplink_sheet)
        
        # Đọc sheet DC nếu có
        df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC']) if 'DC' in sheet_map else pd.DataFrame()

        return gdf_points, df_uplink, df_dc, None
    except Exception as e:
        return None, None, None, f"Lỗi đọc file: {e}"

# ==========================================
# 3. XÂY DỰNG ĐỒ THỊ TỪ CHUỖI UPLINK (=>)
# ==========================================
def build_network_graph(gdf_points, df_uplink, df_dc):
    G = nx.Graph()
    
    name_col = 'name' if 'name' in gdf_points.columns else gdf_points.columns[0]
    
    # 1. Thêm tất cả điểm từ GeoJSON vào Đồ thị
    for _, row in gdf_points.iterrows():
        node_id = str(row[name_col]).strip()
        if node_id:
            G.add_node(node_id, pos=(row.geometry.y, row.geometry.x))

    # 2. Bóc tách chuỗi Uplink (dạng A=>B=>C)
    # Tìm cột chứa thông số Uplink
    uplink_col = None
    for col in df_uplink.columns:
        if 'UPLINK' in str(col).upper() or 'THÔNG SỐ' in str(col).upper():
            uplink_col = col
            break
    if uplink_col is None and len(df_uplink.columns) >= 2:
        uplink_col = df_uplink.columns[1] # Mặc định lấy cột B như trong hình

    if uplink_col:
        for _, row in df_uplink.iterrows():
            route_str = str(row[uplink_col])
            if '=>' in route_str:
                # Tách chuỗi theo dấu =>
                raw_nodes = route_str.split('=>')
                # Làm sạch tên từng điểm
                clean_nodes = [clean_node_name(n) for n in raw_nodes if 'Trung Gian' not in n]
                clean_nodes = [n for n in clean_nodes if n] # Bỏ chuỗi rỗng
                
                # Tạo các cạnh liên kết giữa các điểm kế tiếp
                for i in range(len(clean_nodes) - 1):
                    u, v = clean_nodes[i], clean_nodes[i+1]
                    if u != v:
                        G.add_edge(u, v)

    # 3. Cập nhật chiều dài cáp từ sheet DC (nếu có)
    if not df_dc.empty and len(df_dc.columns) >= 3:
        for _, row in df_dc.iterrows():
            u = clean_node_name(str(row.iloc[0]))
            v = clean_node_name(str(row.iloc[1]))
            try:
                length = float(row.iloc[2])
                if G.has_edge(u, v):
                    G[u][v]['length'] = length
                elif G.has_node(u) and G.has_node(v):
                    G.add_edge(u, v, length=length)
            except:
                pass

    return G

# ==========================================
# 4. TÌM ĐƯỜNG VÀ BÓC TÁCH TỌA ĐỘ
# ==========================================
def find_path_and_coords(graph, start_node, end_node):
    # Chuẩn hóa tên đầu vào
    s_clean = clean_node_name(start_node)
    e_clean = clean_node_name(end_node)
    
    if not graph.has_node(s_clean):
        return None, None, f"Không tìm thấy điểm đo gốc: {start_node} trong GeoJSON!"
    if not graph.has_node(e_clean):
        return None, None, f"Không tìm thấy điểm định hướng: {end_node} trong GeoJSON!"

    try:
        # Tìm đường đi ngắn nhất qua liên kết Uplink
        path = nx.shortest_path(graph, source=s_clean, target=e_clean)
        
        path_coords = []
        path_segments = []
        
        for i in range(len(path) - 1):
            u, v = path[i], path[i+1]
            pos_u = graph.nodes[u].get('pos')
            pos_v = graph.nodes[v].get('pos')
            
            if pos_u and pos_v:
                if i == 0:
                    path_coords.append(pos_u)
                path_coords.append(pos_v)
                
                length = graph[u][v].get('length', None)
                path_segments.append({'coords': [pos_u, pos_v], 'length': length})
                
        return path_coords, path_segments, None
    except nx.NetworkXNoPath:
        return None, None, f"Không tìm thấy đường nối liên tục từ {start_node} đến {end_node} trong chuỗi Uplink!"

# ==========================================
# 5. GIAO DIỆN STREAMLIT CHÍNH
# ==========================================
st.title("📍 Hệ thống Tính toán & Hiển thị Mạng lưới Tập điểm")

geojson_file = "data.geojson"
excel_file = "Data.xlsx"

gdf_pts, df_uplink, df_dc, err = load_data(geojson_file, excel_file)

if err:
    st.error(err)
else:
    name_col_json = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    list_points = sorted(gdf_pts[name_col_json].astype(str).unique().tolist())

    # Sidebar
    st.sidebar.header("Thông tin đo đạc")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach_input = st.sidebar.number_input("Khoảng cách đo (m):", min_value=0.0, value=500.0, step=1.0)
    btn_calc = st.sidebar.button("Tính toán & Vẽ bản đồ")

    # Bản đồ vệ tinh Google
    m = folium.Map(
        location=[gdf_pts.geometry.y.mean(), gdf_pts.geometry.x.mean()], 
        zoom_start=16, 
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
        attr='Google Satellite'
    )

    # Hiển thị tất cả tập điểm
    marker_cluster = plugins.MarkerCluster().add_to(m)
    for _, row in gdf_pts.iterrows():
        name = str(row[name_col_json])
        coord = (row.geometry.y, row.geometry.x)
        folium.CircleMarker(
            location=coord, radius=4, color="yellow", fill=True, fill_color="yellow", fill_opacity=0.9, tooltip=name
        ).add_to(marker_cluster)

    # Xử lý tính toán
    if btn_calc and td_do and td_huong:
        graph = build_network_graph(gdf_pts, df_uplink, df_dc)
        path_coords, path_segments, path_err = find_path_and_coords(graph, td_do, td_huong)

        if path_err:
            st.error(path_err)
        else:
            st.success(f"📌 Đã bóc tách thành công tuyến liên kết Uplink từ **{td_do}** đến **{td_huong}**")
            
            # 1. Vẽ đường Uplink liên kết (Xanh lam đậm)
            if path_coords:
                folium.PolyLine(path_coords, color="#00FFFF", weight=5, opacity=0.9, tooltip="Đường Uplink kết nối").add_to(m)

            # 2. Hiển thị độ dài cáp từ DC lên các đoạn
            for seg in path_segments:
                if seg['length']:
                    mid_lat = (seg['coords'][0][0] + seg['coords'][1][0]) / 2
                    mid_lon = (seg['coords'][0][1] + seg['coords'][1][1]) / 2
                    folium.Marker(
                        [mid_lat, mid_lon],
                        icon=folium.DivIcon(html=f'<div style="font-size: 10pt; color: yellow; font-weight: bold; background-color: rgba(0,0,0,0.6); padding: 2px 4px;">{seg["length"]}m</div>')
                    ).add_to(m)

            # 3. Đánh dấu điểm Gốc & Hướng
            start_pos = graph.nodes[clean_node_name(td_do)]['pos']
            end_pos = graph.nodes[clean_node_name(td_huong)]['pos']
            
            folium.Marker(start_pos, popup=f"Gốc: {td_do}", icon=folium.Icon(color="green", icon="play")).add_to(m)
            folium.Marker(end_pos, popup=f"Định hướng: {td_huong}", icon=folium.Icon(color="red", icon="star")).add_to(m)

            # 4. Đường đo đạc đỏ nét đứt + Nhãn khoảng cách
            folium.PolyLine([start_pos, end_pos], color="red", weight=3, dash_array='5, 10').add_to(m)
            
            mid_meas = [(start_pos[0] + end_pos[0])/2, (start_pos[1] + end_pos[1])/2]
            folium.Marker(
                mid_meas,
                icon=folium.DivIcon(html=f'<div style="font-size: 12pt; color: white; font-weight: bold; background-color: rgba(255,0,0,0.8); padding: 2px 6px; border-radius: 3px;">{khoang_cach_input}m</div>')
            ).add_to(m)

            m.location = mid_meas

    folium_static(m, width=1100, height=650)
