import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import time
# --- ใช้แค่ qrcode ตัวเดียว (ไม่ต้องใช้ promptpay แล้ว) ---
import qrcode
from io import BytesIO
import json

# --- ตั้งค่าหน้าเว็บ ---
st.set_page_config(page_title="ร้านชาบูเสียบไม้", page_icon="🍢", layout="wide")

# ==========================================
# --- 🔧 ฟังก์ชันสร้าง PromptPay (เขียนเอง ไม่ง้อ Library) ---
# ==========================================
def crc16(data):
    # สูตรคำนวณ Checksum ของ PromptPay (CRC-16/CCITT-FALSE)
    crc = 0xFFFF
    for i in range(len(data)):
        x = ((crc >> 8) ^ ord(data[i])) & 0xFF
        x ^= x >> 4
        crc = ((crc << 8) ^ (x << 12) ^ (x << 5) ^ x) & 0xFFFF
    return "{:04X}".format(crc)

def generate_promptpay(id_or_phone, amount=None):
    # 1. ตรวจสอบเบอร์โทรหรือเลขบัตร
    target = id_or_phone.replace("-", "").strip()
    if len(target) == 10 and target.startswith("0"):
        target = "0066" + target[1:] # แปลงเบอร์มือถือ 08x -> 668x
        type_id = "01"
    else:
        type_id = "02" # กรณีเลขบัตรประชาชน

    # 2. ประกอบร่างข้อมูล (EMVCo Standard)
    data = [
        "000201",             # ID Payload Format
        "010212" if amount else "010211", # Point of Initiation (12=Dynamic, 11=Static)
        "2937",               # Merchant Account Info Header
        "0016A000000677010111", # AID (PromptPay ID)
        type_id + "{:02}".format(len(target)) + target # เบอร์โทร/เลขบัตร
    ]
    
    # 3. คำนวณความยาว Merchant Info (ID 29) ใหม่ให้เป๊ะ
    merchant_data = data[3] + data[4]
    data[2] = "29{:02}".format(len(merchant_data))
    
    # 4. ข้อมูลส่วนที่เหลือ
    payload_list = [
        data[0], data[1], data[2] + merchant_data,
        "5802TH",             # Country
        "5303764",            # Currency (THB)
    ]
    
    # 5. ใส่ยอดเงิน (ถ้ามี)
    if amount:
        amt_str = "{:.2f}".format(float(amount))
        payload_list.append("54{:02}".format(len(amt_str)) + amt_str)
    
    # 6. เตรียมคำนวณ Checksum
    payload_list.append("6304")
    raw_data = "".join(payload_list)
    
    # 7. เติม Checksum
    return raw_data + crc16(raw_data)

# ==========================================
# --- ตั้งค่าเบอร์ร้านตรงนี้ ---
# ==========================================
MY_PROMPTPAY = "0946264635"  # <--- ⚠️ อย่าลืมแก้เบอร์ตรงนี้นะครับ!

# ==========================================
# --- CSS Styling (เหมือนเดิม) ---
# ==========================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;600&display=swap');
    :root { --primary: #FF4B4B; --bg-color: #F8F9FA; }
    .stApp { background-color: var(--bg-color); font-family: 'Prompt', sans-serif !important; }
    h1, h2, h3, h4, div, span, p, button { font-family: 'Prompt', sans-serif !important; }
    
    /* การ์ดเมนู */
    .food-card {
        background: white; border-radius: 20px;
        box-shadow: 0 10px 25px rgba(0,0,0,0.05);
        overflow: hidden; transition: 0.3s; border: 1px solid white;
        height: 100%; display: flex; flex-direction: column;
    }
    .food-card:hover { transform: translateY(-5px); border-color: var(--primary); }
    .food-img-container { height: 180px; background: #f0f0f0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
    .food-img { width: 100%; height: 100%; object-fit: cover; }
    .food-info { padding: 15px; text-align: center; flex-grow: 1; }
    
    /* ปุ่มต่างๆ */
    .stButton>button { border-radius: 50px; font-weight: 600; width: 100%; }
    .cart-btn-style button { background: var(--primary); color: white; border: none; box-shadow: 0 4px 15px rgba(255, 75, 75, 0.4); }
    
    header, footer {visibility: hidden;}
</style>
""", unsafe_allow_html=True)

# --- เชื่อมต่อ Google Sheets ---
@st.cache_resource
def init_connection():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 1. ลองเช็คว่ามี Secret ของระบบ Cloud ไหม?
    if 'gcp_service_account' in st.secrets:
        creds_dict = st.secrets["gcp_service_account"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    # 2. ถ้าไม่มี (แปลว่ารันในคอมตัวเอง) ให้หาไฟล์ credentials.json
    else:
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
    
    client = gspread.authorize(creds)
    return client

try:
    client = init_connection()
    sh = client.open("ShabuDB")
    sheet_orders = sh.sheet1
    sheet_menu = sh.worksheet("Menu")
except Exception as e:
    st.error(f"เชื่อมต่อ Database ไม่ได้: {e}")
    st.stop()

# --- State ---
if 'cart' not in st.session_state:
    st.session_state.cart = {}

# ดึงเมนู
try:
    menu_data = sheet_menu.get_all_records()
except:
    menu_data = []

menu_price_dict = {row['Item']: row['Price'] for row in menu_data}

# ==========================================
# --- Pop-up Cart (Dialog) ---
# ==========================================
@st.dialog("🛒 ตะกร้าสินค้าของคุณ")
def show_cart_dialog():
    if not st.session_state.cart:
        st.info("ยังไม่มีสินค้าในตะกร้า")
        return

    total_price = 0
    order_details = []
    
    with st.container(border=True):
        for item, qty in st.session_state.cart.items():
            if item in menu_price_dict:
                price = menu_price_dict[item]
                row_total = price * qty
                total_price += row_total
                
                c1, c2, c3 = st.columns([3, 1, 1])
                c1.write(f"**{item}**")
                c2.write(f"x{qty}")
                c3.write(f"{row_total}.-")
                order_details.append(f"{item}x{qty}")
        
        st.divider()
        st.markdown(f"<h3 style='text-align: right; color: #FF4B4B;'>รวม: {total_price} บาท</h3>", unsafe_allow_html=True)

    # --- ส่วนแสดง QR Code (ใช้ฟังก์ชันเขียนเอง) ---
    if total_price > 0:
        st.write("")
        st.markdown("##### 📲 สแกนจ่ายเงิน (PromptPay)")
        
        # 1. สร้างข้อความ PromptPay
        pp_text = generate_promptpay(MY_PROMPTPAY, total_price)
        
        # 2. สร้างรูป QR
        img = qrcode.make(pp_text)
        buf = BytesIO()
        img.save(buf)
        
        col_qr, col_txt = st.columns([1, 2])
        with col_qr:
            st.image(buf, caption="สแกนได้เลย", use_container_width=True)
        with col_txt:
            st.success(f"ยอดชำระ: {total_price} บาท")
            st.caption(f"โอนเข้าเบอร์: {MY_PROMPTPAY}")
            st.caption("สแกนปุ๊บ ยอดขึ้นอัตโนมัติครับ ✨")
    # -------------------------------------------

    st.write("")
    with st.form("checkout_form"):
        st.write("**ข้อมูลจัดส่ง / เรียกคิว**")
        name = st.text_input("ชื่อลูกค้า", placeholder="เช่น พี่สมชาย...")
        phone = st.text_input("เบอร์โทร", placeholder="08x-xxxxxxx")
        
        if st.form_submit_button("✅ ยืนยันการสั่งซื้อ (โอนแล้ว)", type="primary"):
            if name and phone:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                all_values = sheet_orders.get_all_values()
                queue_no = len(all_values)
                order_str = ", ".join(order_details)
                
                sheet_orders.append_row([queue_no, timestamp, name, phone, order_str, total_price, "รอคิว"])
                
                st.success(f"สั่งซื้อสำเร็จ! คิว #{queue_no}")
                st.session_state.cart = {}
                time.sleep(1.5)
                st.rerun()
            else:
                st.error("กรุณากรอกชื่อและเบอร์โทรครับ")
                
    if st.button("❌ ล้างตะกร้าทั้งหมด"):
        st.session_state.cart = {}
        st.rerun()

# ==========================================
# --- Main UI ---
# ==========================================

# Header
col_brand, col_cart = st.columns([4, 1], gap="small")
with col_brand:
    st.markdown("""
    <div style="display: flex; align-items: center;">
        <img src="https://cdn-icons-png.flaticon.com/512/7603/7603240.png" width="60" style="margin-right: 15px;">
        <div>
            <h1 style="margin:0; font-size: 2.2rem; color: #FF4B4B;">ร้านชาบูเสียบไม้</h1>
            <p style="margin:0; color: gray;">ความอร่อย...ที่คุณเลือกได้เอง 😋</p>
        </div>
    </div>
    """, unsafe_allow_html=True)

with col_cart:
    item_count = sum(st.session_state.cart.values())
    btn_label = f"🛒 ตะกร้า ({item_count})" if item_count > 0 else "🛒 ตะกร้า"
    st.markdown('<div class="cart-btn-style">', unsafe_allow_html=True)
    if st.button(btn_label, use_container_width=True):
        show_cart_dialog()
    st.markdown('</div>', unsafe_allow_html=True)

st.divider()

if not menu_data:
    st.warning("ร้านยังไม่เปิดเมนูครับ")
    st.stop()

# Tabs หมวดหมู่
all_categories = sorted(list(set([row.get('Category', 'อื่นๆ') for row in menu_data if row.get('Category')])))
tab_names = ["ทั้งหมด"] + all_categories
tabs = st.tabs(tab_names)

for tab, category in zip(tabs, tab_names):
    with tab:
        if category == "ทั้งหมด":
            filtered_menu = menu_data
        else:
            filtered_menu = [d for d in menu_data if d.get('Category') == category]
        
        if not filtered_menu:
            st.info("ไม่มีเมนูในหมวดนี้")
        else:
            cols = st.columns(4)
            for i, row in enumerate(filtered_menu):
                item_name = row['Item']
                item_price = row['Price']
                item_img = row.get('Image', '')
                if not item_img: item_img = "https://cdn-icons-png.flaticon.com/512/1046/1046751.png"

                with cols[i % 4]:
                    st.markdown(f"""
                    <div class="food-card">
                        <div class="food-img-container">
                            <img src="{item_img}" class="food-img">
                        </div>
                        <div class="food-info">
                            <div class="food-name">{item_name}</div>
                            <div class="food-price">{item_price}.-</div>
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if st.button(f"ใส่ตะกร้า ➕", key=f"btn_{category}_{i}"):
                        st.session_state.cart[item_name] = st.session_state.cart.get(item_name, 0) + 1
                        st.toast(f'เพิ่ม "{item_name}" แล้ว!')
                        time.sleep(0.1)
                        st.rerun()