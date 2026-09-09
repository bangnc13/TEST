import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from streamlit_folium import folium_static
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
    if not raw_name or pd.isna(raw_name):
        return ""
    
    name = str(raw_name).strip()
    # Cắt bỏ phần cổng phía sau (/16, /6, /4, /5...)
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

    # Duyệt qua các hàng trong Excel để tìm dòng Uplink chứa cả điểm Gốc và Định hướng
    for _, row in df_uplink.iterrows():
        route_str = str(row[uplink_col]) if pd.notna(row[uplink_col]) else ""
        if '=>' in route_str:
            raw_nodes = route_str.split('=>')
            clean_nodes = [clean_node_name(n) for n in raw_nodes if 'Trung Gian' not in str(n)]
            
            # Loại bỏ các phần tử rỗng và các phần tử trùng nhau liên tiếp
            final_nodes = []
            for n in clean_nodes:
                if n and (not final_nodes or final_nodes[-1] != n):
                    final_nodes.append(n)

            # Kiểm tra xem dòng này có chứa cả 2 điểm cần tìm không
            if s_clean in final_nodes and e_clean in final_nodes:
                idx_s = final_nodes.index(s_clean)
                idx_e = final_nodes.index(e_clean)
                
                # Cắt lấy danh sách các điểm nằm giữa 2 điểm chọn
                if idx_s <= idx_e:
                    sub_route = final_nodes[idx_s : idx_e + 1]
                else:
                    sub_route = final_nodes[idx_e : idx_s + 1][::-1]
                    
                return sub_route, None

    return None, f"Không tìm thấy dòng Uplink chứa cả 2 tập điểm {start_node} và {end_node}!"

# ==========================================
# 4. GIAO DIỆN STREAMLIT CHÍNH
# ==========================================
st.title("📍 Hệ thống Tính toán & Hiển thị Mạng lưới Tập điểm")

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
    st.sidebar.header("Thông tin đo đạc")
    td_do = st.sidebar.selectbox("Tập điểm đo (Gốc):", list_points)
    td_huong = st.sidebar.selectbox("Tập điểm định hướng:", [p for p in list_points if p != td_do])
    khoang_cach_input = st.sidebar.number_input("Khoảng cách đo (m):", min_value=0.0, value=500.0, step=1.0)
    btn_calc = st.sidebar.button("Tính toán & Vẽ bản đồ")

    # Bản đồ mặc định
    m = folium.Map(
        location=[gdf_pts.geometry.y.mean(), gdf_pts.geometry.x.mean()], 
        zoom_start=15, 
        tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', 
        attr='Google Satellite'
    )

    # Nếu BẤM NÚT: Chỉ hiển thị các tập điểm thuộc tuyến được bóc tách
    if btn_calc and td_do and td_huong:
        route_nodes, route_err = extract_route_from_uplink(df_uplink, td_do, td_huong)

        if route_err:
            st.error(route_err)
        else:
            st.success(f"📌 Tuyến Uplink bóc tách ({len(route_nodes)} tập điểm): " + " ➔ ".join(route_nodes))

            route_coords = []
            missing_nodes = []

            # 1. Vẽ CHỈ CÁC TẬP ĐIỂM nằm trong tuyến Uplink này
            for node in route_nodes:
                if node in coord_dict:
                    pos = coord_dict[node]
                    route_coords.append(pos)
                    
                    # Phân loại điểm Gốc / Định hướng / Trung gian để đổi màu icon
                    if node == clean_node_name(td_do):
                        icon_color = "green"
                    elif node == clean_node_name(td_huong):
                        icon_color = "red"
                    else:
                        icon_color = "blue"

                    # Đánh dấu Marker điểm
                    folium.CircleMarker(
                        location=pos,
                        radius=5,
                        color=icon_color,
                        fill=True,
                        fill_color=icon_color,
                        fill_opacity=1.0,
                        tooltip=node
                    ).add_to(m)

                    # Hiển thị TÊN TẬP ĐIỂM trực tiếp lên bản đồ (Giống hình 1)
                    folium.Marker(
                        location=pos,
                        icon=folium.DivIcon(
                            html=f'<div style="font-size: 9pt; color: #00FFFF; font-weight: bold; font-family: Arial; text-shadow: 1px 1px 2px black; white-space: nowrap;">{node}</div>',
                            icon_anchor=(-8, 10)
                        )
                    ).add_to(m)
                else:
                    missing_nodes.append(node)

            if missing_nodes:
                st.warning(f"⚠️ Một số điểm trong Uplink không có tọa độ trong GeoJSON: {', '.join(missing_nodes)}")

            # 2. Vẽ đường cáp nối liên tục qua tất cả các điểm trong tuyến (Màu đỏ/Xanh lơ)
            if len(route_coords) >= 2:
                folium.PolyLine(
                    route_coords, 
                    color="#FF4500", 
                    weight=4, 
                    opacity=0.9, 
                    tooltip="Đường cáp Uplink"
                ).add_to(m)

                # 3. Đường đo đạc đỏ nét đứt nối trực tiếp Gốc -> Định hướng
                start_pos = route_coords[0]
                end_pos = route_coords[-1]
                folium.PolyLine([start_pos, end_pos], color="yellow", weight=2, dash_array='6, 6').add_to(m)

                # Nhãn hiển thị khoảng cách đo đạc
                mid_meas = [(start_pos[0] + end_pos[0])/2, (start_pos[1] + end_pos[1])/2]
                folium.Marker(
                    mid_meas,
                    icon=folium.DivIcon(html=f'<div style="font-size: 11pt; color: white; font-weight: bold; background-color: rgba(255,0,0,0.85); padding: 3px 6px; border-radius: 4px;">{khoang_cach_input}m</div>')
                ).add_to(m)

                # Tự động điều chỉnh góc nhìn (Fit Bounds) vừa khít tuyến cáp
                m.fit_bounds(route_coords)

    else:
        # Nếu chưa bấm nút: Hiển thị chấm mờ toàn bộ tập điểm để quan sát tổng quan
        for name, pos in coord_dict.items():
            folium.CircleMarker(
                location=pos, radius=3, color="gray", fill=True, fill_color="gray", fill_opacity=0.5, tooltip=name
            ).add_to(m)

    folium_static(m, width=1100, height=650)
