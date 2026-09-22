import json
import os

def process_ring_json(input_path="RING.json", output_path="RING_processed.json"):
    """
    Hàm xử lý và chuẩn hóa file GeoJSON RING.json:
    - Sửa các giá trị null ở thuộc tính tên/mã.
    - Chuẩn hóa thuộc tính 'name' và 'TEN_TAP_DIEM' / 'TEN_DOAN_CAP' cho phù hợp với Streamlit.
    - Lọc bỏ các bản ghi không có tọa độ hợp lệ.
    """
    if not os.path.exists(input_path):
        print(f"❌ Lỗi: Không tìm thấy file '{input_path}'!")
        return

    print(f"🔍 Đang đọc file {input_path}...")
    with open(input_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"❌ Lỗi định dạng JSON: {e}")
            return

    # 1. Kiểm tra cấu trúc GeoJSON
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        print("⚠️ Cảnh báo: File không đúng chuẩn GeoJSON FeatureCollection. Đang thử đóng gói lại...")
        if isinstance(data, list):
            features_list = data
        else:
            features_list = data.get("features", [])
        data = {
            "type": "FeatureCollection",
            "features": features_list
        }

    features = data.get("features", [])
    updated_points = 0
    updated_lines = 0
    skipped = 0

    print(f"⚙️ Đang xử lý {len(features)} đối tượng...")

    for idx, feature in enumerate(features):
        geom = feature.get("geometry") or {}
        props = feature.get("properties") or {}
        g_type = geom.get("type")
        coords = geom.get("coordinates")

        # Bỏ qua nếu không có tọa độ
        if not coords:
            skipped += 1
            continue

        # 2. Xử lý Tập điểm (Point)
        if g_type == "Point":
            # Lấy tên từ các trường có sẵn
            raw_name = (
                props.get("name") or 
                props.get("TEN_TAP_DIEM") or 
                props.get("startdevicename") or 
                props.get("enddevicename") or 
                props.get("code") or 
                props.get("id")
            )
            
            # Xử lý nếu giá trị bị null hoặc rỗng
            if raw_name is None or str(raw_name).strip().lower() in ["null", "none", "", "nan"]:
                clean_name = f"TAP_DIEM_AUTO_{idx + 1}"
                updated_points += 1
            else:
                clean_name = str(raw_name).strip()

            # Gán lại chuẩn hóa vào properties
            props["name"] = clean_name
            props["TEN_TAP_DIEM"] = clean_name

        # 3. Xử lý Đoạn cáp (LineString / MultiLineString)
        elif g_type in ["LineString", "MultiLineString"]:
            raw_name = (
                props.get("name") or 
                props.get("TEN_DOAN_CAP") or 
                props.get("cable_name") or 
                props.get("code") or 
                props.get("id")
            )

            # Xử lý nếu giá trị bị null hoặc rỗng
            if raw_name is None or str(raw_name).strip().lower() in ["null", "none", "", "nan"]:
                clean_name = f"CAP_AUTO_{idx + 1}"
                updated_lines += 1
            else:
                clean_name = str(raw_name).strip()

            # Gán lại chuẩn hóa vào properties
            props["name"] = clean_name
            props["TEN_DOAN_CAP"] = clean_name

            # Sửa các trường điểm đầu/cuối nếu có bị null
            if "startdevicename" in props and (props["startdevicename"] is None or str(props["startdevicename"]).lower() in ["null", "none"]):
                props["startdevicename"] = "CHUA_XAC_DINH"
            if "enddevicename" in props and (props["enddevicename"] is None or str(props["enddevicename"]).lower() in ["null", "none"]):
                props["enddevicename"] = "CHUA_XAC_DINH"

        feature["properties"] = props

    # 4. Lưu ra file mới
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("--------------------------------------------------")
    print("✅ ĐÃ XỬ LÝ HOÀN TẤT!")
    print(f"📍 Tập điểm (Point) đã sửa lỗi/bổ sung tên: {updated_points}")
    print(f"🛣️ Đoạn cáp (LineString) đã sửa lỗi/bổ sung tên: {updated_lines}")
    print(f"⚠️ Bản ghi bỏ qua (thiếu tọa độ): {skipped}")
    print(f"💾 File mới đã được lưu tại: {output_path}")

if __name__ == "__main__":
    process_ring_json("RING.json", "RING.json")  # Ghi đè trực tiếp lên file RING.json
