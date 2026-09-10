import streamlit as st
import pandas as pd
import json
import re
import math
import folium
from streamlit_folium import st_folium

# 1. Hàm tính khoảng cách giữa 2 tọa độ (mét)
def geodetic_distance(coord1, coord2):
    R = 6371000.0  # Bán kính Trái Đất (m)
    lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

# 2. Hàm nội suy tìm tọa độ điểm đo cách Start Node một khoảng d (mét)
def get_point_at_distance(path_coords, target_distance):
    if not path_coords:
        return None
    if target_distance <= 0:
        return path_coords[0]
        
    accumulated = 0.0
    for i in range(len(path_coords) - 1):
        p1, p2 = path_coords[i], path_coords[i+1]
        seg_dist = geodetic_distance(p1, p2)
        if seg_dist == 0:
            continue
        if accumulated + seg_dist >= target_distance:
            ratio = (target_distance - accumulated) / seg_dist
            lat = p1[0] + ratio * (p2[0] - p1[0])
            lon = p1[1] + ratio * (p2[1] - p1[1])
            return [lat, lon]
        accumulated += seg_dist
    return path_coords[-1]

# Cấu hình trang
st.set_page_config(page_title="Hệ thống Đo & Tra cứu Tuyến Cáp Quang", layout="wide")
st.title("🛰️ Hệ thống Đo & Tra cứu Tuyến Cáp Quang")

# Đọc dữ liệu
@st.cache_data
def load_data():
    bc_df = pd.read_excel('BC.xlsx', sheet_name='Splitter')
    dc_df = pd.read_excel('DC.xlsx', sheet_name='Cable')
    with open('data.geojson', 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
    return bc_df, dc_df, geojson_data

bc_df, dc_df, geojson_data = load_data()

# Lookup table tọa độ
coords_dict = {}
for ft in geojson_data['features']:
    props = ft.get('properties', {})
    name = props.get('name')
    geom = ft.get('geometry', {})
    if name and geom.get('type') == 'Point':
        coords_dict[name] = [geom['coordinates'][1], geom['coordinates'][0]]

cable_lengths = {}
for _, row in dc_df.iterrows():
    p1, p2 = str(row['Điểm KN1']).strip(), str(row['Điểm KN2']).strip()
    length = row['Chiều dài thực (m)']
    if pd.notnull(length):
        cable_lengths[(p1, p2)] = float(length)
        cable_lengths[(p2, p1)] = float(length)

def parse_uplink_chain(uplink_str):
    if pd.isna(uplink_str): return []
    items = str(uplink_str).split('=>')
    nodes = []
    for item in items:
        node = re.split(r'/\d+$', item.strip())[0]
        if node and node != 'Trung Gian' and node not in nodes:
            nodes.append(node)
    return nodes

all_nodes = set()
for uplink in bc_df['Thông số Uplink'].dropna():
    all_nodes.update(parse_uplink_chain(uplink))

# --- SIDEBAR ---
st.sidebar.header("📍 Thông tin đo kiểm")
start_node = st.sidebar.selectbox("Tập điểm đang đo (Điểm bắt đầu):", options=[""] + sorted(list(all_nodes)))

related_nodes = set()
if start_node:
    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        if start_node in chain:
            related_nodes.update(chain)
    related_nodes.discard(start_node)

target_node = st.sidebar.selectbox("Đo về Tập điểm (Điểm đích):", options=[""] + sorted(list(related_nodes)))
measured_length = st.sidebar.number_input("Chiều dài đoạn cáp đo được (mét):", min_value=0.0, value=0.0, step=10.0)

# 🔘 NÚT BẤM THỂ HIỆN KẾT QUẢ ĐO LÊN MAP
btn_show_map = st.sidebar.button("📌 Thể hiện kết quả đo lên Map", type="primary")

# Tự động lưu trạng thái bấm nút
if 'show_measured_point' not in st.session_state:
    st.session_state.show_measured_point = False

if btn_show_map:
    st.session_state.show_measured_point = True

# --- XỬ LÝ & HIỂN THỊ ---
if start_node and target_node:
    path_found = []
    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        if start_node in chain and target_node in chain:
            idx1, idx2 = chain.index(start_node), chain.index(target_node)
            path_found = chain[idx1:idx2+1] if idx1 <= idx2 else chain[idx2:idx1+1][::-1]
            break

    if path_found:
        total_cable_len = sum(cable_lengths.get((path_found[i], path_found[i+1]), 0.0) for i in range(len(path_found)-1))
        final_accumulated_length = total_cable_len + measured_length

        st.subheader("📊 Kết quả tính toán khoảng cách")
        c1, c2, c3 = st.columns(3)
        c1.metric("Tổng chiều dài cáp cơ sở", f"{total_cable_len:,.1f} m")
        c2.metric("Chiều dài cáp đo thêm", f"{measured_length:,.1f} m")
        c3.metric("Tổng chiều dài tích lũy", f"{final_accumulated_length:,.1f} m")

        # Chuẩn bị tọa độ tuyến
        path_coords = [coords_dict[node] for node in path_found if node in coords_dict]

        map_center = path_coords[0] if path_coords else [21.8, 105.2]
        m = folium.Map(location=map_center, zoom_start=16, tiles=None)

        folium.TileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='Google Street').add_to(m)
        folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite').add_to(m)

        # Vẽ Marker các tập điểm
        for node in path_found:
            if node in coords_dict:
                color = "red" if node in [start_node, target_node] else "blue"
                folium.Marker(coords_dict[node], popup=f"<b>{node}</b>", tooltip=node, icon=folium.Icon(color=color)).add_to(m)

        # Vẽ tuyến cáp
        if len(path_coords) > 1:
            folium.PolyLine(path_coords, color="red", weight=4, opacity=0.8).add_to(m)

        # 🎯 HIỂN THỊ ĐIỂM ĐO TRÊN MAP KHI BẤM NÚT
        if st.session_state.show_measured_point and measured_length > 0 and path_coords:
            measured_coord = get_point_at_distance(path_coords, measured_length)
            if measured_coord:
                folium.Marker(
                    location=measured_coord,
                    popup=f"<b>Vị trí đo đạc / Sự cố</b><br>Cách {start_node}: {measured_length}m",
                    tooltip=f"📍 Vị trí đo: {measured_length}m",
                    icon=folium.Icon(color="orange", icon="warning-sign")
                ).add_to(m)
                
                # Tự động zoom đến vị trí đo đạc
                m.location = measured_coord
                st.success(f"📍 Đã định vị thành công điểm đo cách {start_node} khoảng **{measured_length}m** trên bản đồ!")

        folium.LayerControl().add_to(m)
        st_folium(m, width=1100, height=550)
else:
    st.info("Vui lòng chọn đầy đủ **Tập điểm đang đo** và **Đo về Tập điểm**.")
