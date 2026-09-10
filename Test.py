import streamlit as st
import pandas as pd
import json
import re
import math
import folium
from streamlit_folium import st_folium

# --- 1. HÀM CHUẨN HÓA TÊN NÚT / TẬP ĐIỂM ---
def normalize_node_name(name):
    if not name or not isinstance(name, str):
        return ""
    # Chuyển các chuỗi dạng .0023/ thành .023/ hoặc ngược lại để đồng bộ số lượng chữ số
    # Xóa bớt số 0 thừa sau dấu chấm (ví dụ: .0023 -> .023)
    normalized = re.sub(r'\.0+(\d+)', r'.0\1', name.strip())
    return normalized

# Read Data & Cache
@st.cache_data
def load_data():
    bc_df = pd.read_excel('BC.xlsx', sheet_name='Splitter')
    dc_df = pd.read_excel('DC.xlsx', sheet_name='Cable')
    with open('data.geojson', 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
    return bc_df, dc_df, geojson_data

bc_df, dc_df, geojson_data = load_data()

# Tạo Dictionary Tọa độ (Chuẩn hóa cả key)
coords_dict = {}
for ft in geojson_data['features']:
    props = ft.get('properties', {})
    name = props.get('name')
    geom = ft.get('geometry', {})
    if name and geom.get('type') == 'Point':
        lat, lon = geom['coordinates'][1], geom['coordinates'][0]
        coords_dict[name.strip()] = [lat, lon]
        coords_dict[normalize_node_name(name)] = [lat, lon]

# Bổ sung khoảng cách cáp
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
        # Bỏ phần /1, /2 ở cuối cổng
        node = re.split(r'/\d+$', item.strip())[0]
        if node and node != 'Trung Gian' and node not in nodes:
            nodes.append(node)
    return nodes

# Xử lý Logic Tìm Tuyến
path_found = []
if start_node and target_node:
    norm_start = normalize_node_name(start_node)
    norm_target = normalize_node_name(target_node)

    for uplink in bc_df['Thông số Uplink'].dropna():
        chain = parse_uplink_chain(uplink)
        norm_chain = [normalize_node_name(n) for n in chain]
        
        # Kiểm tra theo tên gốc hoặc tên đã chuẩn hóa
        if (start_node in chain or norm_start in norm_chain) and \
           (target_node in chain or norm_target in norm_chain):
            
            idx1 = chain.index(start_node) if start_node in chain else norm_chain.index(norm_start)
            idx2 = chain.index(target_node) if target_node in chain else norm_chain.index(norm_target)
            
            path_found = chain[idx1:idx2+1] if idx1 <= idx2 else chain[idx2:idx1+1][::-1]
            break

    # Lấy tọa độ tuyến cáp
    path_coords = []
    for node in path_found:
        coord = coords_dict.get(node) or coords_dict.get(normalize_node_name(node))
        if coord:
            path_coords.append(coord)

    # Tự động Tự căn chỉnh Zoom (Fit Bounds) khi có tọa độ
    if path_coords:
        map_center = path_coords[0]
        # Thêm tự động zoom vừa khít tuyến cáp vào Map
        m.fit_bounds(path_coords)
