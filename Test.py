import streamlit as st
import pandas as pd
import json
import re
import folium
from streamlit_folium import st_folium
import urllib.parse

# 1. Cấu hình trang Streamlit
st.set_page_config(page_title="Hệ thống Đo & Tra cứu Cáp Quang", layout="wide")
st.title("🛰️ Hệ thống Đo & Tra cứu Tuyến Cáp Quang")

# 2. Đọc và xử lý dữ liệu
@st.cache_data
def load_data():
    # Đọc file Excel
    bc_df = pd.read_excel('BC.xlsx', sheet_name='Splitter')
    dc_df = pd.read_excel('DC.xlsx', sheet_name='Cable')
    
    # Đọc file GeoJSON
    with open('data.geojson', 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
        
    return bc_df, dc_df, geojson_data

bc_df, dc_df, geojson_data = load_data()

# Tạo lookup table tọa độ từ GeoJSON
coords_dict = {}
geojson_features = {}

for ft in geojson_data['features']:
    props = ft.get('properties', {})
    name = props.get('name')
    geom = ft.get('geometry', {})

    if name:
        geojson_features[name] = ft
        if geom.get('type') == 'Point':
            # GeoJSON lưu [lon, lat]
            coords_dict[name] = [geom['coordinates'][1], geom['coordinates'][0]]

# Xây dựng bảng tra khoảng cách thực tế giữa các điểm KN (Cột Y trong DC - Cable)
cable_lengths = {}
for _, row in dc_df.iterrows():
    p1 = str(row['Điểm KN1']).strip()
    p2 = str(row['Điểm KN2']).strip()
    length = row['Chiều dài thực (m)']
    if pd.notnull(length):
        cable_lengths[(p1, p2)] = float(length)
        cable_lengths[(p2, p1)] = float(length)

# 3. Hàm phân tích chuỗi Uplink
def parse_uplink_chain(uplink_str):
    if pd.isna(uplink_str):
        return []
    items = str(uplink_str).split('=>')
    nodes = []
    for item in items:
        # Lấy tên tập điểm trước dấu / (bỏ qua số cổng phía sau)
        node = re.split(r'/\d+$', item.strip())[0]
        if node and node != 'Trung Gian' and node not in nodes:
            nodes.append(node)
    return nodes

# Lập danh sách tất cả các tập điểm từ Uplink
all_nodes = set()
for uplink in bc_df['Thông số Uplink'].dropna():
    chain = parse_uplink_chain(uplink)
    all_nodes.update(chain)
sorted_nodes = sorted(list(all_nodes))

# 4. Giao diện Sidebar chọn điểm & nhập khoảng cách
st.sidebar.header("📍 Thông tin đo kiểm")

start_node = st.sidebar.selectbox("Tập điểm đang đo (Điểm bắt đầu):", options=[""] + sorted_nodes)

# Tìm danh sách tập điểm liên quan dựa trên chuỗi Uplink
related_nodes = set()
if start_node:
    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        if start_node in chain:
            related_nodes.update(chain)
    related_nodes.discard(start_node)

target_node = st.sidebar.selectbox("Đo về Tập điểm (Điểm đích):", options=[""] + sorted(list(related_nodes)))
measured_length = st.sidebar.number_input("Chiều dài đoạn cáp đo được (mét):", min_value=0.0, value=0.0, step=1.0)

# 5. Xử lý tính toán & Hiển thị Bản đồ
if start_node and target_node:
    # Tìm tuyến kết nối giữa Start Node và Target Node từ các chuỗi Uplink
    path_found = []
    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        if start_node in chain and target_node in chain:
            idx1 = chain.index(start_node)
            idx2 = chain.index(target_node)
            if idx1 <= idx2:
                path_found = chain[idx1:idx2+1]
            else:
                path_found = chain[idx2:idx1+1][::-1]
            break

    if path_found:
        # Tính tổng chiều dài theo định mức thực tế từ sheet Cable
        total_cable_len = 0.0
        segment_details = []
        for i in range(len(path_found)-1):
            n1, n2 = path_found[i], path_found[i+1]
            l = cable_lengths.get((n1, n2), 0.0)
            total_cable_len += l
            segment_details.append(f"{n1} ➔ {n2}: {l}m")

        final_accumulated_length = total_cable_len + measured_length

        # Hiển thị kết quả tính toán
        st.subheader("📊 Kết quả tính toán khoảng cách")
        c1, c2, c3 = st.columns(3)
        c1.metric("Tổng chiều dài cáp cơ sở", f"{total_cable_len:,.1f} m")
        c2.metric("Chiều dài cáp đo thêm", f"{measured_length:,.1f} m")
        c3.metric("Tổng chiều dài tích lũy", f"{final_accumulated_length:,.1f} m", delta_color="normal")

        # Nút Mở Chỉ dẫn Google Maps
        start_coord = coords_dict.get(start_node)
        target_coord = coords_dict.get(target_node)

        if start_coord and target_coord:
            gmap_url = f"https://www.google.com/maps/dir/?api=1&origin={start_coord[0]},{start_coord[1]}&destination={target_coord[0]},{target_coord[1]}&travelmode=driving"
            st.markdown(f'👉 [**Mở ứng dụng Google Maps chỉ đường**]({gmap_url})', unsafe_allow_html=True)

        # Tạo Bản đồ với Folium
        map_center = start_coord if start_coord else [21.8, 105.2]
        m = folium.Map(location=map_center, zoom_start=15, tiles=None)

        # Lớp bản đồ Google Maps Street & Satellite
        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
            attr='Google',
            name='Google Maps (Đường phố)',
            overlay=False,
            control=True
        ).add_to(m)

        folium.TileLayer(
            tiles='https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
            attr='Google',
            name='Google Maps (Vệ tinh)',
            overlay=False,
            control=True
        ).add_to(m)

        # Vẽ đường nối cáp thực tế từ GeoJSON hoặc nối thẳng nếu GeoJSON không chứa Geometry tuyến
        path_coords = []
        for node in path_found:
            if node in coords_dict:
                coord = coords_dict[node]
                path_coords.append(coord)
                # Vẽ Marker cho các điểm
                icon_color = "red" if node in [start_node, target_node] else "blue"
                folium.Marker(
                    location=coord,
                    popup=f"<b>{node}</b>",
                    tooltip=node,
                    icon=folium.Icon(color=icon_color, icon="info-sign")
                ).add_to(m)

        if len(path_coords) > 1:
            folium.PolyLine(
                locations=path_coords,
                color="red",
                weight=5,
                opacity=0.8,
                tooltip=f"Tuyến cáp: {' ➔ '.join(path_found)}"
            ).add_to(m)

        folium.LayerControl().add_to(m)
        st_folium(m, width=1100, height=550)

    else:
        st.warning("Không tìm thấy đường liên kết Uplink trực tiếp giữa 2 tập điểm này.")
else:
    st.info("Vui lòng chọn **Tập điểm đang đo** và **Đo về Tập điểm** ở thanh bên trái để bắt đầu.")
