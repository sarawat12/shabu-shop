import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
from gtts import gTTS
from io import BytesIO
import json

# --- 1. ตั้งค่าหน้าเว็บ + กันจอขาว (Google Translate Bug) ---
st.set_page_config(page_title="ระบบจัดการร้าน", page_icon="📢", layout="wide")

st.markdown("""
    <meta name="google" content="notranslate">
    <style>
        .VIpgJd-ZVi9od-ORHb-OEVmcd { display: none !important; }
    </style>
""", unsafe_allow_html=True)

# --- 2. ฟังก์ชันเชื่อมต่อ Database (รองรับ Cloud) ---
@st.cache_resource
def init_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # เช็คว่ารันบน Cloud หรือ คอมตัวเอง
    if 'gcp_service_account' in st.secrets:
        creds_dict = st.secrets["gcp_service_account"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    
    client = gspread.authorize(creds)
    return client

try:
    client = init_connection()
    sh = client.open("ShabuDB")
    sheet_orders = sh.sheet1
    sheet_menu = sh.worksheet("Menu")
    sheet_history = sh.worksheet("History")
except Exception as e:
    st.error(f"เชื่อมต่อ Database ไม่ได้: {e}")
    st.stop()

# --- ระบบเสียง (แบบเล่นซ้ำได้) ---
if 'last_sound' not in st.session_state:
    st.session_state.last_sound = None
if 'last_msg' not in st.session_state:
    st.session_state.last_msg = ""

def make_sound(text):
    try:
        tts = gTTS(text=text, lang='th')
        sound_file = BytesIO()
        tts.write_to_fp(sound_file)
        return sound_file
    except Exception as e:
        st.error(f"Sound Error: {e}")
        return None

# --- ส่วน Login ---
st.title("📢 ระบบจัดการร้าน (Admin)")

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

if not st.session_state.logged_in:
    pwd = st.text_input("รหัสผ่าน:", type="password")
    if st.button("เข้าสู่ระบบ"):
        if pwd == "1234":
            st.session_state.logged_in = True
            st.rerun()
        else:
            st.error("รหัสผิด")
    st.stop()

if st.button("ออกจากระบบ"):
    st.session_state.logged_in = False
    st.rerun()

st.divider()

# --- แถบเล่นเสียง (อยู่บนสุด) ---
if st.session_state.last_sound:
    st.info(f"🔊 ประกาศล่าสุด: {st.session_state.last_msg}")
    st.audio(st.session_state.last_sound, format='audio/mp3')

# --- Tabs ทำงาน ---
tab1, tab2 = st.tabs(["📋 จัดการออเดอร์", "✏️ แก้ไขเมนู"])

# === TAB 1: ออเดอร์ ===
with tab1:
    st.header("รายการออเดอร์")
    try:
        data = sheet_orders.get_all_records()
    except:
        data = []
    
    if data:
        st.dataframe(pd.DataFrame(data), use_container_width=True)
        st.markdown("---")
        
        c1, c2 = st.columns([2, 1])
        
        with c1:
            st.subheader("📢 เรียกคิว")
            pending = [d for d in data if d['Status'] == 'รอคิว']
            
            if pending:
                options = [f"คิว {d['QueueID']} ({d['Name']})" for d in pending]
                q_select = st.selectbox("เลือกคิวที่ทำเสร็จ:", options)
                
                if st.button("อาหารเสร็จแล้ว (ประกาศ) 🔊", type="primary"):
                    q_id = int(q_select.split(" ")[1])
                    q_name = q_select.split("(")[1].replace(")", "")
                    
                    try:
                        # อัปเดต Excel
                        cell = sheet_orders.find(str(q_id), in_column=1)
                        sheet_orders.update_cell(cell.row, 7, "เสร็จแล้ว")
                        
                        # สร้างเสียง
                        msg = f"ขอเชิญคิวที่ {q_id} คุณ {q_name} ค่ะ อาหารได้แล้วค่ะ"
                        sound_data = make_sound(msg)
                        
                        if sound_data:
                            st.session_state.last_sound = sound_data
                            st.session_state.last_msg = msg
                        
                        st.success(f"อัปเดตคิว {q_id} เรียบร้อย!")
                        time.sleep(1)
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"เกิดข้อผิดพลาด: {e}")
            else:
                st.info("ไม่มีออเดอร์ค้าง")
        
        with c2:
            st.subheader("🌙 ปิดยอด")
            with st.expander("กดเมื่อปิดร้าน"):
                if st.button("ย้ายเข้า History"):
                    all_rows = sheet_orders.get_all_values()
                    if len(all_rows) > 1:
                        sheet_history.append_rows(all_rows[1:])
                        sheet_orders.clear()
                        sheet_orders.append_row(["QueueID", "Time", "Name", "Phone", "Items", "Total", "Status"])
                        st.success("ปิดยอดเรียบร้อย")
                        time.sleep(1)
                        st.rerun()

# === TAB 2: แก้ไขเมนู ===
with tab2:
    st.header("จัดการเมนู")
    try:
        current_menu = sheet_menu.get_all_records()
    except:
        current_menu = []
        
    st.dataframe(pd.DataFrame(current_menu), use_container_width=True)
    
    c1, c2 = st.columns(2)
    with c1:
        with st.form("add"):
            n = st.text_input("ชื่อเมนู")
            p = st.number_input("ราคา", min_value=1)
            i = st.text_input("ลิงก์รูป")
            c = st.selectbox("หมวดหมู่", ["หมู", "ไก่", "เนื้อ", "ผัก", "ลูกชิ้น", "ทานเล่น", "เครื่องดื่ม"])
            if st.form_submit_button("เพิ่ม"):
                sheet_menu.append_row([n, p, i, c])
                st.rerun()
    with c2:
        if current_menu:
            items = [r['Item'] for r in current_menu]
            d = st.selectbox("ลบเมนู", items)
            if st.button("ลบ"):
                cell = sheet_menu.find(d, in_column=1)
                sheet_menu.delete_rows(cell.row)
                st.rerun()