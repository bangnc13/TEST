import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
import fiona

# Đăng ký driver đọc file KML/KMZ cho geopandas/fiona
fiona.drvsupport.supported_drivers['KML'] = 'rw'

st.set_page_config(layout="wide", page_title="Xác định điểm đứt cáp - Streamlit")

st.title("📍 Hệ thống xác định vị trí đứt cáp viễn thông")

# Sidebar - Tải file dữ liệu
st.sidebar.header("📂 Tải lên dữ liệu đầu vào")
file_geojson = st.sidebar.file_uploader("1. data.geojson (Tập điểm)", type=["geojson", "json"])
file_excel = st.sidebar.file_uploader("2. Data(1).xlsx (Sheet Uplink & Đoạn cáp)", type=["xlsx"])
file_kml_cot = st.sidebar.file_uploader("3. KML Cột điện (Tùy chọn)", type=["kml"])
file_kml_tuyen = st.sidebar.file_uploader("4. duong_day.kml (Tùy chọn)", type=["kml"])

def parse_kml(uploaded_file):
    """Hàm bổ trợ đọc file KML tải lên"""
    if uploaded_file is not None:
        try:
            return gpd.read_file(uploaded_file, driver='KML')
        except Exception as e:
            st.sidebar.error(f"Không thể đọc KML: {e}")
            return None
    return None

if file_geojson and file_excel:
    try:
        # 1. Đọc dữ liệu
        gdf_tap_diem = gpd.read_file(file_geojson)
        df_uplink = pd.read_excel(file_excel, sheet_name="Uplink")
        df_doan_cap = pd.read_excel(file_excel, sheet_name="Đoạn cáp")
        
        gdf_cot = parse_kml(file_kml_cot)
        gdf_tuyen = parse_kml(file_kml_tuyen)

        st.sidebar.success("Tải dữ liệu thành công!")

        # Tìm cột tên cho tập điểm
        col_name = None
        for col in ['name', 'Name', 'TEN_TAP_DIEM', 'MaTapDiem', 'id']:
            if col in gdf_tap_diem.columns:
                col_name = col
                break
        if not col_name:
            col_name = gdf_tap_diem.columns[0]

        # 2. Form chọn thông tin sự cố
        st.sidebar.header("🔍 Nhập thông tin đo OTDR")
        danh_sach_diem = sorted(gdf_tap_diem[col_name].dropna().unique().tolist())
        
        diem_do = st.sidebar.selectbox("Chọn điểm đo (Ví dụ: TQGP001.0001/H1):", danh_sach_diem)
        khoang_cach_do = st.sidebar.number_input("Khoảng cách đo được từ OTDR (mét):", min_value=0.0, value=150.0, step=10.0)

        # 3. Xử lý tính toán khi nhấn nút
        if st.sidebar.button("Nội suy vị trí đứt"):
            # Lấy thông tin điểm đo đầu tiên
            row_start = gdf_tap_diem[gdf_tap_diem[col_name] == diem_do]
            
            if not row_start.empty:
                geom_start = row_start.geometry.values[0]
                lon_start, lat_start = geom_start.x, geom_start.y

                # Tra cứu Uplink để biết hướng đi tiếp theo
                # Giả định cột tìm kiếm trong Uplink là 'DiemDau' và 'DiemCuoi' hoặc tương tự
                col_uplink_from = [c for c in df_uplink.columns if 'dau' in c.lower() or 'from' in c.lower() or 'start' in c.lower()]
                col_uplink_to = [c for c in df_uplink.columns if 'cuoi' in c.lower() or 'to' in c.lower() or 'end' in c.lower()]

                c_from = col_uplink_from[0] if col_uplink_from else df_uplink.columns[0]
                c_to = col_uplink_to[0] if col_uplink_to else df_uplink.columns[1]

                row_up = df_uplink[df_uplink[c_from].astype(str) == str(diem_do)]

                if not row_up.empty:
                    diem_tiep_theo = row_up[c_to].values[0]
                    row_next = gdf_tap_diem[gdf_tap_diem[col_name] == diem_tiep_theo]

                    if not row_next.empty:
                        geom_next = row_next.geometry.values[0]
                        lon_next, lat_next = geom_next.x, geom_next.y

                        # Tra cứu chiều dài đoạn cáp trong sheet Đoạn cáp
                        # Giả định có cột độ dài
                        col_len = [c for c in df_doan_cap.columns if 'dai' in c.lower() or 'length' in c.lower()]
                        col_len_name = col_len[0] if col_len else df_doan_cap.columns[-1]

                        # Lấy chiều dài định mức/thực tế của đoạn cáp
                        chieu_dai_doan = 500.0  # Mặc định nếu không tìm thấy
                        if not df_doan_cap.empty:
                            matching_cap = df_doan_cap[
                                (df_doan_cap.iloc[:, 0].astype(str) == str(diem_do)) | 
                                (df_doan_cap.iloc[:, 1].astype(str) == str(diem_do))
                            ]
                            if not matching_cap.empty:
                                chieu_dai_doan = float(matching_cap[col_len_name].values[0])

                        # Tính tỷ lệ khoảng cách đứt
                        ratio = min(khoang_cach_do / chieu_dai_doan, 1.0) if chieu_dai_doan > 0 else 0
                        lat_break = lat_start + ratio * (lat_next - lat_start)
                        lon_break = lon_start + ratio * (lon_next - lon_start)

                        # Hiển thị kết quả ra màn hình
                        st.subheader("📌 Kết quả xác định vị trí:")
                        col1, col2, col3 = st.columns(3)
                        col1.metric("Tập điểm đo", diem_do)
                        col2.metric("Hướng về tập điểm", diem_tiep_theo)
                        col3.metric("Tọa độ đứt cáp", f"{lat_break:.6f}, {lon_break:.6f}")

                        # 4. Hiển thị bản đồ Folium
                        m = folium.Map(location=[lat_start, lon_start], zoom_start=17)

                        # Vẽ tuyến KML nếu có
                        if gdf_tuyen is not None:
                            folium.GeoJson(gdf_tuyen, name="Đường dây KML", style_function=lambda x: {'color': 'blue', 'weight': 2}).add_to(m)

                        # Vẽ cột điện KML nếu có
                        if gdf_cot is not None:
                            for _, row in gdf_cot.iterrows():
                                if row.geometry and row.geometry.type == 'Point':
                                    folium.CircleMarker(
                                        location=[row.geometry.y, row.geometry.x],
                                        radius=3, color='gray', fill=True
                                    ).add_to(m)

                        # Đánh dấu các tập điểm & Điểm đứt
                        folium.Marker(
                            [lat_start, lon_start],
                            popup=f"Điểm đo: {diem_do}",
                            icon=folium.Icon(color='blue', icon='play')
                        ).add_to(m)

                        folium.Marker(
                            [lat_next, lon_next],
                            popup=f"Tập điểm đích: {diem_tiep_theo}",
                            icon=folium.Icon(color='green', icon='stop')
                        ).add_to(m)

                        folium.Marker(
                            [lat_break, lon_break],
                            popup=f"💥 VỊ TRÍ ĐỨT ({khoang_cach_do}m)",
                            icon=folium.Icon(color='red', icon='warning-sign')
                        ).add_to(m)

                        # Đoạn cáp kết nối
                        folium.PolyLine([(lat_start, lon_start), (lat_next, lon_next)], color="gray", weight=3, dash_array='5, 5').add_to(m)
                        folium.PolyLine([(lat_start, lon_start), (lat_break, lon_break)], color="red", weight=5).add_to(m)

                        # Xuất bản đồ trên Streamlit
                        st_folium(m, width="100%", height=550)

                    else:
                        st.error(f"Không tìm thấy tọa độ của tập điểm kế tiếp: {diem_tiep_theo}")
                else:
                    st.error(f"Không tìm thấy luồng Uplink xuất phát từ điểm đo {diem_do}")
            else:
                st.error("Không tìm thấy dữ liệu tập điểm đã chọn.")
    except Exception as e:
        st.error(f"Đã xảy ra lỗi trong quá trình xử lý dữ liệu: {e}")
else:
    st.info("👋 Vui lòng tải lên file `data.geojson` và `Data(1).xlsx` ở thanh bên trái để bắt đầu.")
