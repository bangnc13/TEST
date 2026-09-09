import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium import plugins
from streamlit_folium import folium_static
from pyproj import Geod
import networkx as nx  # Thư viện cao cấp để xử lý đồ thị mạng lưới
import os

st.set_page_config(layout="wide", page_title="Hệ thống Quản lý & Hiển thị Mạng lưới Cáp")

# ==========================================
# 1. HÀM NẠP DỮ LIỆU (CÓ CACHE)
# ==========================================
@st.cache_data
def load_data(geojson_path, excel_path):
    # Kiểm tra file tồn tại
    if not os.path.exists(geojson_path) or not os.path.exists(excel_path):
        return None, None, None, f"Không tìm thấy file data.geojson hoặc Data.xlsx!"

    try:
        # A. Đọc dữ liệu không gian từ GeoJSON
        gdf_points = gpd.read_file(geojson_path)
        
        # B. Đọc dữ liệu thuộc tính từ Excel
        excel_file = pd.ExcelFile(excel_path)
        sheet_names = excel_file.sheet_names
        # Chuẩn hóa tên sheet sang CHỮ HOA để dễ so sánh
        sheet_map = {str(s).strip().upper(): s for s in sheet_names}
        
        # Đọc dữ liệu kết nối mạng lưới (uplink)
        uplink_sheet_name = sheet_map.get('UPLINK', sheet_names[0])
        df_uplink = pd.read_excel(excel_path, sheet_name=uplink_sheet_name)
        
        # Đọc dữ liệu chiều dài cáp (DC) - Sheet chứa dữ kiện đoạn cáp
        if 'DC' in sheet_map:
            df_dc = pd.read_excel(excel_path, sheet_name=sheet_map['DC'])
        else:
            df_dc = pd.DataFrame() # Trả về DataFrame rỗng nếu không có sheet DC

        return gdf_points, df_uplink, df_dc, None
        
    except Exception as e:
        return None, None, None, f"Lỗi khi đọc file: {e}"

# ==========================================
# 2. THUẬT TOÁN XỬ LÝ ĐỒ THỊ (NETWORKX)
# ==========================================
# Hàm tạo đồ thị mạng lưới từ dữ liệu uplink và DC
def build_network_graph(gdf_points, df_uplink, df_dc):
    G = nx.Graph() # Tạo đồ thị vô hướng
    
    # Xác định cột tên điểm trong JSON (ưu tiên 'name' hoặc cột đầu tiên)
    name_col = 'name' if 'name' in gdf_points.columns else gdf_points.columns[0]
    
    # 2.1 Thêm tất cả các điểm (Nodes) và tọa độ vào đồ thị
    for _, row in gdf_points.iterrows():
        point_id = str(row[name_col])
        if point_id: # Kiểm tra tên điểm không rỗng
            G.add_node(point_id, pos=(row.geometry.y, row.geometry.x))
            
    # 2.2 Thêm các kết nối (Edges) từ sheet UPLINK
    # Cần xác định đúng tên 2 cột chứa điểm nối (ví dụ: 'Point A' và 'Point B')
    # Giả sử là 2 cột đầu tiên của sheet uplink
    uplink_cols = df_uplink.columns
    if len(uplink_cols) >= 2:
        for _, row in df_uplink.iterrows():
            u = str(row[uplink_cols[0]]).strip()
            v = str(row[uplink_cols[1]]).strip()
            # Chỉ thêm kết nối nếu cả 2 điểm đều tồn tại trong danh sách điểm
            if G.has_node(u) and G.has_node(v):
                G.add_edge(u, v)

    # 2.3 Cập nhật chiều dài cáp từ sheet DC (dữ kiện các đoạn cáp)
    # Cần xác định đúng tên 2 cột điểm nối và cột chiều dài (ví dụ: 'From', 'To', 'Length')
    dc_cols = df_dc.columns
    if len(dc_cols) >= 3:
        for _, row in df_dc.iterrows():
            u = str(row[dc_cols[0]]).strip()
            v = str(row[dc_cols[1]]).strip()
            length = row[dc_cols[2]]
            
            # Cập nhật thuộc tính 'length' cho cạnh hiện có
            if G.has_edge(u, v):
                G[u][v]['length'] = length
            # Hoặc tạo cạnh mới nếu sheet DC có kết nối mà sheet Uplink không có
            elif G.has_node(u) and G.has_node(v):
                 G.add_edge(u, v, length=length)
                    
    return G

# Hàm tìm đường đi liên tục từ điểm gốc đến điểm định hướng (Sử dụng thuật toán cao cấp Dijsktra)
def find_path_with_data(graph, start_point, end_point):
    if not graph.has_node(start_point) or not graph.has_node(end_point):
        return None, None, "Một trong hai tập điểm đo không tồn tại trong hệ thống dữ liệu!"
        
    try:
        # Sử dụng thuật toán Dijkstra để tìm đường đi ngắn nhất dựa trên số đoạn cáp
        path = nx.shortest_path(graph, source=start_point, target=end_point)
        
        # Bóc tách tọa độ và tính toán dữ liệu
        path_coords = []
        path_segments = [] # Chứa thông tin từng đoạn cáp trên đường đi
        
        for i in range(len(path) - 1):
            u, v = path[i], path[i+1]
            u_coord = graph.nodes[u]['pos']
            v_coord = graph.nodes[v]['pos']
            
            if i == 0: path_coords.append(u_coord)
            path_coords.append(v_coord)
            
            # Lấy chiều dài từ sheet DC đã nạp vào đồ thị
            length = graph[u][v].get('length', None) # None nếu không tìm thấy trong sheet DC
            path_segments.append({'from': u, 'to': v, 'coords': [u_coord, v_coord], 'length': length})
            
        return path_coords, path_segments, None
        
    except nx.NetworkXNoPath:
        return None, None, f"Không tìm thấy đường cáp kết nối liên tục từ {start_point} đến {end_point} dựa trên dữ liệu 'uplink'!"
    except Exception as e:
        return None, None, f"Lỗi không xác định: {e}"

# ==========================================
# 3. GIAO DIỆN CHÍNH STREAMLIT
# ==========================================
st.title("📍 Hệ thống Tính toán & Hiển thị Mạng lưới Tập điểm")

# Đường dẫn file mặc định (cùng thư mục với Test.py)
geojson_file = "data.geojson"
excel_file = "Data.xlsx"

# Nạp dữ liệu và kiểm tra lỗi
gdf_pts, df_uplink, df_dc, load_error = load_data(geojson_file, excel_file)

if load_error:
    st.error(load_error)
else:
    # Xử lý lấy danh sách tập điểm từ JSON để hiển thị lên Sidebar
    name_col_json = 'name' if 'name' in gdf_pts.columns else gdf_pts.columns[0]
    # Sắp xếp danh sách tập điểm theo tên để dễ chọn
    sorted_points = gdf_pts.sort_values(by=name_col_json)
    list_points = sorted_points[name_col_json].astype(str).tolist()

    # --- SIDEBAR: Thông tin đo đạc ---
    st.sidebar.header("Thông tin đo đạc")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    
    # Khoảng cách chỉ dùng để hiển thị trên đường nối đo đạc đỏ, không dùng trong thuật toán mạng lưới
    khoang_cach_input = st.sidebar.number_input("Khoảng cách đo (m):", min_value=0.0, value=500.0, step=1.0)
    
    btn_calc = st.sidebar.button("Tính toán & Vẽ bản đồ")

    # --- BẮT ĐẦU VẼ BẢN ĐỒ ---
    # Khởi tạo bản đồ Folium với nền VỆ TINH mặc định (Google Satellite)
    m = folium.Map(
        location=[gdf_pts.geometry.y.mean(), gdf_pts.geometry.x.mean()], 
        zoom_start=17, 
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
        attr='Google Satellite'
    )
    
    # 3.1 Vẽ TOÀN BỘ các tập điểm lên bản đồ
    marker_cluster = plugins.MarkerCluster().add_to(m) # Sử dụng cụm để tránh rối
    for _, row in gdf_pts.iterrows():
        name = str(row[name_col_json])
        coord = (row.geometry.y, row.geometry.x)
        
        folium.CircleMarker(
            location=coord, 
            radius=4, 
            color="yellow", 
            fill=True, 
            fill_color="yellow",
            fill_opacity=0.9,
            tooltip=f"Tập điểm: {name}"
        ).add_to(marker_cluster)

    # Xử lý tính toán khi nhấn nút
    if btn_calc and td_do and td_huong:
        st.info(f"Đang tính toán mạng lưới và đường nối...")
        
        # Bước 1: Xây dựng đồ thị mạng lưới dựa trên dữ liệu Uplink và DC
        graph = build_network_graph(gdf_pts, df_uplink, df_dc)
        
        # Bước 2: Bóc tách đường đi cáp liên tục giữa điểm Gốc và điểm Định hướng (sử dụng Dijkstra)
        path_coords, path_segments, path_error = find_path_with_data(graph, td_do, td_huong)
        
        if path_error:
             st.error(path_error)
        else:
            st.success(f"📌 Đã tìm thấy đường cáp liên tục nối từ {td_do} đến {td_huong}")
            
            # --- 3.2 Vẽ đường liên kết MẠNG LƯỚI (Màu xanh, dựa trên 'uplink' và 'DC') ---
            # Vẽ đường cáp liên tục dạng nét đứt màu xanh đậm
            folium.PolyLine(path_coords, color="#1E90FF", weight=4, opacity=0.9, dash_array='8, 12').add_to(m)
            
            # Bóc tách và vẽ các đoạn cáp nhỏ, HIỂN THỊ CHIỀU DÀI CÁP LÊN BẢN ĐỒ (dựa trên dữ kiện DC)
            for segment in path_segments:
                coords = segment['coords']
                length = segment['length']
                
                # Hiển thị chiều dài cáp (dùng DivIcon để tạo nhãn văn bản cố định)
                if length is not None:
                    # Tính điểm giữa của đoạn cáp để đặt nhãn
                    mid_lat = (coords[0][0] + coords[1][0]) / 2
                    mid_lon = (coords[0][1] + coords[1][1]) / 2
                    
                    folium.Marker(
                        [mid_lat, mid_lon],
                        icon=folium.DivIcon(html=f'<div style="font-size: 11pt; color: white; font-weight: bold; background-color: rgba(0,0,0,0.5); padding: 1px 4px; border-radius: 3px;">{length}m</div>')
                    ).add_to(m)

            # --- 3.3 Đánh dấu Điểm Gốc và Điểm Định hướng (Giống ảnh gốc) ---
            start_coord = graph.nodes[td_do]['pos']
            end_coord = graph.nodes[td_huong]['pos']
            
            # Đánh dấu Điểm Gốc (Marker màu xanh lá)
            folium.Marker(start_coord, popup=f"Gốc: {td_do}", icon=folium.Icon(color="green", icon="play")).add_to(m)
            # Đánh dấu Điểm Định hướng (Marker màu đỏ có ngôi sao)
            folium.Marker(end_coord, popup=f"Định hướng: {td_huong}", icon=folium.Icon(color="red", icon="star")).add_to(m)

            # --- 3.4 Vẽ đường ĐO ĐẠC (Màu đỏ nét đứt, hiển thị khoảng cách đo) ---
            # Đường nối trực tiếp nét đứt màu đỏ (dash_array)
            folium.PolyLine([start_coord, end_coord], color="red", weight=3, opacity=0.8, dash_array='5, 10').add_to(m)
            
            # Hiển thị Khoảng cách đo đo được giữa đoạn đường này
            mid_lat_meas = (start_coord[0] + end_coord[0]) / 2
            mid_lon_meas = (start_coord[1] + end_coord[1]) / 2
            folium.Marker(
                [mid_lat_meas, mid_lon_meas],
                icon=folium.DivIcon(html=f'<div style="font-size: 12pt; color: white; font-weight: bold; background-color: rgba(255,0,0,0.7); padding: 2px 5px; border-radius: 4px;">{khoang_cach_input}m</div>')
            ).add_to(m)
            
            # Tự động di chuyển bản đồ đến vùng kết quả
            m.location = [mid_lat_meas, mid_lon_meas]
            
    # Hiển thị bản đồ Folium lên giao diện Streamlit
    folium_static(m, width=1100, height=650)
