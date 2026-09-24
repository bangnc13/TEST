import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
import fiona

# Đăng ký driver đọc KML
fiona.drvsupport.supported_drivers['KML'] = 'rw'

st.set_page_config(layout="wide", page_title="Xác định vị trí đứt cáp")

st.title("📍 Hệ thống xác định vị trí đứt cáp viễn thông")

# Sidebar - Tải file dữ liệu
st.sidebar.header("📂 Tải lên dữ liệu đầu vào")
file_geojson = st.sidebar.file_uploader("1. data.geojson (Tập điểm)", type=["geojson", "json"])
file_excel = st.sidebar.file_uploader("2. Data(1).xlsx (Tải file Excel)", type=["xlsx"])
file_kml_cot = st.sidebar.file_uploader("3. KML Cột điện (Tùy chọn)", type=["kml"])
file_kml_tuyen = st.sidebar.file_uploader("4. duong_day.kml (Tùy chọn)", type=["kml"])

def parse_kml(uploaded_file):
    if uploaded_file is not None:
        try:
            return gpd.read_file(uploaded_file, driver='KML')
        except Exception:
            return None
    return None

def find_column_by_keywords(df, keywords):
    """Hàm tìm cột phù hợp theo từ khóa"""
    for col in df.columns:
        col_str = str(col).lower().strip()
        if any(kw in col_str for kw in keywords):
            return col
    return None

if file_geojson and file_excel:
    try:
        # Đọc danh sách tất cả các sheet trong file Excel
        xls = pd.ExcelFile(file_excel)
        sheet_names = xls.sheet_names
        
        sheet_uplink_name = None
        sheet_doancap_name = None

        for s in sheet_names:
            s_clean = str(s).strip().lower()
            if 'uplink' in s_clean:
                sheet_uplink_name = s
            elif any(kw in s_clean for kw in ['đoạn cáp', 'doan cap', 'doancap', 'cap']):
                sheet_doancap_name = s

        if not sheet_uplink_name and len(sheet_names) > 0:
            sheet_uplink_name = sheet_names[0]
        if not sheet_doancap_name and len(sheet_names) > 1:
            sheet_doancap_name = sheet_names[1]

        # Đọc dữ liệu
        gdf_tap_diem = gpd.read_file(file_geojson)
        df_uplink = pd.read_excel(xls, sheet_name=sheet_uplink_name) if sheet_uplink_name else pd.DataFrame()
        df_doan_cap = pd.read_excel(xls, sheet_name=sheet_doancap_name) if sheet_doancap_name else pd.DataFrame()

        gdf_cot = parse_kml(file_kml_cot)
        gdf_tuyen = parse_kml(file_kml_tuyen)

        st.sidebar.success(f"Đã đọc xong Excel! (Sheet: '{sheet_uplink_name}')")

        # Xác định cột tên tập điểm trong GeoJSON
        col_name_geojson = find_column_by_keywords(gdf_tap_diem, ['name', 'ten', 'id', 'matapdiem'])
        if not col_name_geojson:
            col_name_geojson = gdf_tap_diem.columns[0]

        # Nhập thông tin sự cố
        st.sidebar.header("🔍 Nhập thông tin đo OTDR")
        danh_sach_diem = sorted(gdf_tap_diem[col_name_geojson].dropna().unique().tolist())
        
        diem_do = st.sidebar.selectbox("Chọn điểm đo:", danh_sach_diem)
        khoang_cach_do = st.sidebar.number_input("Khoảng cách đo được từ OTDR (mét):", min_value=0.0, value=150.0, step=10.0)

        # Xử lý tính toán khi bấm nút
        if st.sidebar.button("Nội suy vị trí đứt"):
            row_start = gdf_tap_diem[gdf_tap_diem[col_name_geojson] == diem_do]
            
            if not row_start.empty:
                geom_start = row_start.geometry.values[0]
                lon_start, lat_start = geom_start.x, geom_start.y

                diem_tiep_theo = None

                # Tìm kiếm điểm tiếp theo trong Sheet Uplink
                if not df_uplink.empty:
                    # Chuyển toàn bộ dữ liệu dạng chuỗi để so sánh chính xác
                    df_up_clean = df_uplink.astype(str).apply(lambda x: x.str.strip())
                    target_str = str(diem_do).strip()

                    # Lặp qua các hàng để tìm hàng chứa 'diem_do'
                    for idx, row in df_up_clean.iterrows():
                        row_vals = row.tolist()
                        if target_str in row_vals:
                            pos = row_vals.index(target_str)
                            # Ưu tiên lấy ô bên phải (hướng xuôi), nếu ở cuối thì lấy ô bên trái (hướng ngược)
                            if pos + 1 < len(row_vals) and row_vals[pos + 1] != 'nan':
                                diem_tiep_theo = row_vals[pos + 1]
                                break
                            elif pos - 1 >= 0 and row_vals[pos - 1] != 'nan':
                                diem_tiep_theo = row_vals[pos - 1]
                                break

                if diem_tiep_theo:
                    row_next = gdf_tap_diem[gdf_tap_diem[col_name_geojson] == diem_tiep_theo]

                    if not row_next.empty:
                        geom_next = row_next.geometry.values[0]
                        lon_next, lat_next = geom_next.x, geom_next.y

                        # Tự động lấy chiều dài đoạn cáp
                        chieu_dai_doan = 500.0  # Mặc định 500m nếu không có dữ liệu
                        if not df_doan_cap.empty:
                            df_cap_clean = df_doan_cap.astype(str).apply(lambda x: x.str.strip())
                            for idx, row in df_cap_clean.iterrows():
                                row_vals = row.tolist()
                                if target_str in row_vals and str(diem_tiep_theo).strip() in row_vals:
                                    # Tìm cột chứa giá trị số (độ dài)
                                    for item in row_vals:
                                        try:
                                            val = float(item)
                                            if val > 0:
                                                chieu_dai_doan = val
                                                break
                                        except ValueError:
                                            continue
                                    break

                        # Nội suy vị trí đứt cáp
                        ratio = min(khoang_cach_do / chieu_dai_doan, 1.0) if chieu_dai_doan > 0 else 0
                        lat_break = lat_start + ratio * (lat_next - lat_start)
                        lon_break = lon_start + ratio * (lon_next - lon_start)

                        # Hiển thị kết quả
                        st.subheader("📌 Kết quả xác định vị trí:")
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Tập điểm đo", diem_do)
                        col2.metric("Hướng về tập điểm", diem_tiep_theo)
                        col3.metric("Tọa độ đứt cáp", f"{lat_break:.6f}, {lon_break:.6f}")

                        # Hiển thị Bản đồ
                        m = folium.Map(location=[lat_start, lon_start], zoom_start=17)

                        if gdf_tuyen is not None:
                            folium.GeoJson(gdf_tuyen, name="Đường dây KML", style_function=lambda x: {'color': 'blue', 'weight': 2}).add_to(m)

                        if gdf_cot is not None:
                            for _, row in gdf_cot.iterrows():
                                if row.geometry and row.geometry.type == 'Point':
                                    folium.CircleMarker(
                                        location=[row.geometry.y, row.geometry.x],
                                        radius=3, color='gray', fill=True
                                    ).add_to(m)

                        folium.Marker([lat_start, lon_start], popup=f"Điểm đo: {diem_do}", icon=folium.Icon(color='blue', icon='play')).add_to(m)
                        folium.Marker([lat_next, lon_next], popup=f"Tập điểm đích: {diem_tiep_theo}", icon=folium.Icon(color='green', icon='stop')).add_to(m)
                        folium.Marker([lat_break, lon_break], popup=f"💥 VỊ TRÍ ĐỨT ({khoang_cach_do}m)", icon=folium.Icon(color='red', icon='warning-sign')).add_to(m)

                        folium.PolyLine([(lat_start, lon_start), (lat_next, lon_next)], color="gray", weight=3, dash_array='5, 5').add_to(m)
                        folium.PolyLine([(lat_start, lon_start), (lat_break, lon_break)], color="red", weight=5).add_to(m)

                        st_folium(m, width="100%", height=550)

                    else:
                        st.error(f"Không tìm thấy tọa độ cho tập điểm kết nối: {diem_tiep_theo}")
                else:
                    st.error(f"Không tìm thấy hướng liên kết nào cho điểm đo: {diem_do} trong Sheet Uplink.")
            else:
                st.error("Không tìm thấy điểm đo trong file GeoJSON.")
    except Exception as e:
        st.error(f"Đã xảy ra lỗi khi xử lý dữ liệu: {e}")
else:
    st.info("👋 Vui lòng tải lên các file dữ liệu ở thanh menu bên trái.")
