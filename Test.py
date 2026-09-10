import streamlit as st
import pandas as pd
import json
import re
import math
import folium
from streamlit_folium import st_folium

# 1. Cấu hình trang Streamlit
st.set_page_config(
    page_title="Hệ thống Đo & Tra cứu Tuyến Cáp Quang",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 2. CSS tối ưu giao diện full tràn viền
st.markdown("""
    <style>
        .main .block-container {
            padding-top: 0rem !important;
            padding-bottom: 0rem !important;
            padding-left: 0rem !important;
            padding-right: 0rem !important;
            max-width: 100% !important;
        }
        div[data-testid="stElementContainer"] has(iframe) {
            height: 100vh !important;
        }
        iframe {
            width: 100% !important;
            height: 100vh !important;
            border: none !important;
        }
    </style>
""", unsafe_allow_html=True)

# 3. Hàm chuẩn hóa tên tập điểm (xử lý lệch số 0: .0023 -> .023)
def normalize_node_name(name):
    if not name or not isinstance(name, str):
        return ""
    return re.sub(r'\.0+(\d+)', r'.0\1', name.strip())

# 4. Các hàm hỗ trợ tính toán khoảng cách & tọa độ
def geodetic_distance(coord1, coord2):
    R = 6371000.0
    lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
    lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

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

# 5. Đọc & Cấu trúc dữ liệu
@st.cache_data
def load_data():
    bc_df = pd.read_excel('BC.xlsx', sheet_name='Splitter')
    dc_df = pd.read_excel('DC.xlsx', sheet_name='Cable')
    with open('data.geojson', 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
    return bc_df, dc_df, geojson_data

bc_df, dc_df, geojson_data = load_data()

coords_dict = {}
for ft in geojson_data['features']:
    props = ft.get('properties', {})
    name = props.get('name')
    geom = ft.get('geometry', {})
    if name and geom.get('type') == 'Point':
        lat, lon = geom['coordinates'][1], geom['coordinates'][0]
        coords_dict[name.strip()] = [lat, lon]
        coords_dict[normalize_node_name(name)] = [lat, lon]

cable_lengths = {}
for _, row in dc_df.iterrows():
    p1, p2 = str(row['Điểm KN1']).strip(), str(row['Điểm KN2']).strip()
    length = row['Chiều dài thực (m)']
    if pd.notnull(length):
        cable_lengths[(p1, p2)] = float(length)
        cable_lengths[(p2, p1)] = float(length)
        cable_lengths[(normalize_node_name(p1), normalize_node_name(p2))] = float(length)
        cable_lengths[(normalize_node_name(p2), normalize_node_name(p1))] = float(length)

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

# 6. GIAO DIỆN SIDEBAR (Định nghĩa biến start_node & target_node trước)
st.sidebar.title("🛰️ Đo & Tra cứu Cáp Quang")
st.sidebar.markdown("---")
st.sidebar.subheader("📍 Thông tin nhập dữ liệu")

start_node = st.sidebar.selectbox("Tập điểm đang đo (Điểm bắt đầu):", options=[""] + sorted(list(all_nodes)))

related_nodes = set()
if start_node:
    norm_start = normalize_node_name(start_node)
    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        norm_chain = [normalize_node_name(n) for n in chain]
        if start_node in chain or norm_start in norm_chain:
            related_nodes.update(chain)
    related_nodes.discard(start_node)

target_node = st.sidebar.selectbox("Đo về Tập điểm (Điểm đích):", options=[""] + sorted(list(related_nodes)))
measured_length = st.sidebar.number_input("Chiều dài đoạn cáp đo được (mét):", min_value=0.0, value=0.0, step=10.0)

btn_show_map = st.sidebar.button("📌 Thể hiện kết quả đo lên Map", type="primary", use_container_width=True)

if 'show_measured_point' not in st.session_state:
    st.session_state.show_measured_point = False

if btn_show_map:
    st.session_state.show_measured_point = True

# 7. LOGIC TÍNH TOÁN VÀ KHỞI TẠO BẢN ĐỒ
path_coords = []
map_center = [21.8, 105.2]
measured_coord = None
path_found = []

if start_node and target_node:
    norm_start = normalize_node_name(start_node)
    norm_target = normalize_node_name(target_node)

    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        norm_chain = [normalize_node_name(n) for n in chain]
        if (start_node in chain or norm_start in norm_chain) and \
           (target_node in chain or norm_target in norm_chain):
            idx1 = chain.index(start_node) if start_node in chain else norm_chain.index(norm_start)
            idx2 = chain.index(target_node) if target_node in chain else norm_chain.index(norm_target)
            path_found = chain[idx1:idx2+1] if idx1 <= idx2 else chain[idx2:idx1+1][::-1]
            break

    if path_found:
        total_cable_len = sum(cable_lengths.get((path_found[i], path_found[i+1]), 0.0) for i in range(len(path_found)-1))
        final_accumulated_length = total_cable_len + measured_length

        st.sidebar.markdown("---")
        st.sidebar.subheader("📊 Kết quả tính toán")
        st.sidebar.metric("Chiều dài cáp cơ sở", f"{total_cable_len:,.1f} m")
        st.sidebar.metric("Chiều dài cáp đo thêm", f"{measured_length:,.1f} m")
        st.sidebar.metric("Tổng chiều dài tích lũy", f"{final_accumulated_length:,.1f} m")

        start_coord = coords_dict.get(start_node) or coords_dict.get(norm_start)
        target_coord = coords_dict.get(target_node) or coords_dict.get(norm_target)
        if start_coord and target_coord:
            gmap_url = f"https://www.google.com/maps/dir/?api=1&origin={start_coord[0]},{start_coord[1]}&destination={target_coord[0]},{target_coord[1]}&travelmode=driving"
            st.sidebar.markdown(f'👉 [**Mở Google Maps chỉ đường**]({gmap_url})')

        for node in path_found:
            coord = coords_dict.get(node) or coords_dict.get(normalize_node_name(node))
            if coord:
                path_coords.append(coord)

        if path_coords:
            map_center = path_coords[0]

        if st.session_state.show_measured_point and measured_length > 0 and path_coords:
            measured_coord = get_point_at_distance(path_coords, measured_length)
            if measured_coord:
                map_center = measured_coord
                st.sidebar.success(f"📍 Đã định vị điểm đo {measured_length}m!")

# 8. VẼ BẢN ĐỒ FOLIUM
m = folium.Map(location=map_center, zoom_start=16, tiles=None)

folium.TileLayer('https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}', attr='Google', name='Google Street').add_to(m)
folium.TileLayer('https://mt1.google.com/vt/lyrs=s&x={x}&y={y}&z={z}', attr='Google', name='Google Satellite').add_to(m)

for node in path_found:
    coord = coords_dict.get(node) or coords_dict.get(normalize_node_name(node))
    if coord:
        color = "red" if node in [start_node, target_node] else "blue"
        folium.Marker(coord, popup=f"<b>{node}</b>", tooltip=node, icon=folium.Icon(color=color)).add_to(m)

if len(path_coords) > 1:
    folium.PolyLine(path_coords, color="red", weight=5, opacity=0.8).add_to(m)

if measured_coord:
    folium.Marker(
        location=measured_coord,
        popup=f"<b>Vị trí đo đạc / Sự cố</b><br>Cách {start_node}: {measured_length}m",
        tooltip=f"📍 Vị trí đo: {measured_length}m",
        icon=folium.Icon(color="orange", icon="warning-sign")
    ).add_to(m)

folium.LayerControl().add_to(m)

# Căn chỉnh tự động góc nhìn vào tuyến cáp
if path_coords:
    m.fit_bounds(path_coords)

st_folium(m, use_container_width=True, height=950)
